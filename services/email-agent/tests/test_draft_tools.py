from __future__ import annotations

from contextlib import contextmanager

from langgraph.store.memory import InMemoryStore

from src.capabilities import attachment_support, current_email_attachments, current_email_id, current_gmail_thread_id
from src.capabilities import draft_tools
from tests.conftest import ai_tool_call, patch_provider, reply_to


@contextmanager
def _thread_context(thread_id: str | None):
    token = current_gmail_thread_id.set(thread_id)
    try:
        yield
    finally:
        current_gmail_thread_id.reset(token)


@contextmanager
def _original_message_context(email_id: str | None, attachments: tuple[dict, ...] = ()):
    id_token = current_email_id.set(email_id)
    attachments_token = current_email_attachments.set(attachments)
    try:
        yield
    finally:
        current_email_attachments.reset(attachments_token)
        current_email_id.reset(id_token)


def test_create_draft_uses_trusted_thread_context(monkeypatch):
    calls: list[dict] = []

    def _create(**kwargs):
        calls.append(kwargs)
        return {"id": "draft-1"}

    patch_provider(monkeypatch, draft_tools, create_draft=_create)

    with _thread_context("thread-1"):
        with reply_to("alice@example.com"):
            result = draft_tools.create_draft.invoke({
                "subject": "Re: question",
                "content": "Draft body",
            })

    assert result == "Created draft 'draft-1' to alice@example.com with subject 'Re: question'."
    assert calls == [{
        "to": "alice@example.com",
        "subject": "Re: question",
        "body": "Draft body",
        "thread_id": "thread-1",
    }]


def test_create_draft_allows_standalone_draft_without_thread(monkeypatch):
    calls: list[dict] = []

    def _create(**kwargs):
        calls.append(kwargs)
        return {"dry_run": True, "action": "create_draft"}

    patch_provider(monkeypatch, draft_tools, create_draft=_create)

    with _thread_context(None):
        with reply_to("alice@example.com"):
            result = draft_tools.create_draft.invoke({
                "subject": "Hello",
                "content": "Draft body",
            })

    assert result == "Created draft to alice@example.com with subject 'Hello'."
    assert calls == [{
        "to": "alice@example.com",
        "subject": "Hello",
        "body": "Draft body",
        "thread_id": None,
    }]


def test_tool_schema_does_not_expose_thread_id():
    schema = draft_tools.create_draft.args_schema.model_json_schema()
    # No "to" either: the draft always replies to the message's own sender.
    assert set(schema["properties"]) == {"subject", "content", "include_attachments"}


def test_tool_node_injects_current_thread_id_for_draft_tools(monkeypatch):
    import src.graph as g

    calls: list[dict] = []
    monkeypatch.setattr(g.settings, "security_enabled", False)
    monkeypatch.setitem(g.tools_by_name_map, "create_draft", draft_tools.create_draft)

    def _create(**kwargs):
        calls.append(kwargs)
        return {"id": "draft-context"}

    patch_provider(monkeypatch, draft_tools, create_draft=_create)

    state = {
        # tool_node derives the recipient from the message's own From header,
        # so the model does not supply one.
        "email_input": {"gmail_thread_id": "thread-context", "author": "A <a@example.com>"},
        "messages": [
            ai_tool_call(
                "create_draft",
                {"subject": "Re", "content": "Body"},
                "call-draft",
            )
        ],
    }

    result = g.tool_node(state, InMemoryStore(), config={"configurable": {"thread_id": "run"}})

    assert calls == [{
        "to": "a@example.com",
        "subject": "Re",
        "body": "Body",
        "thread_id": "thread-context",
    }]
    assert current_gmail_thread_id.get() is None
    assert result["messages"] == [
        {
            "role": "tool",
            "content": "Created draft 'draft-context' to a@example.com with subject 'Re'.",
            "tool_call_id": "call-draft",
        }
    ]


def test_create_draft_reattaches_original_attachments(monkeypatch):
    calls: list[dict] = []
    downloads: list[tuple[str, str]] = []

    def _create(**kwargs):
        calls.append(kwargs)
        return {"id": "draft-1"}

    def _download(message_id, attachment_id):
        downloads.append((message_id, attachment_id))
        return b"file-bytes"

    provider = patch_provider(monkeypatch, draft_tools, create_draft=_create, download_attachment=_download)
    monkeypatch.setattr(attachment_support, "get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(attachment_support, "effective_dry_run", lambda: False)

    attachments = ({
        "filename": "invoice.pdf",
        "mime_type": "application/pdf",
        "size": 10,
        "attachment_id": "att-1",
    },)

    with _original_message_context("msg-1", attachments):
        with reply_to("alice@example.com"):
            result = draft_tools.create_draft.invoke({
                "subject": "Re: invoice",
                "content": "Here it is again.",
                "include_attachments": True,
            })

    assert downloads == [("msg-1", "att-1")]
    assert calls == [{
        "to": "alice@example.com",
        "subject": "Re: invoice",
        "body": "Here it is again.",
        "thread_id": None,
        "attachments": [
            {"filename": "invoice.pdf", "mime_type": "application/pdf", "data": b"file-bytes"}
        ],
    }]
    assert "Attached 1 file(s)" in result


def test_create_draft_skips_attachment_over_size_cap(monkeypatch):
    calls: list[dict] = []

    provider = patch_provider(
        monkeypatch,
        draft_tools,
        create_draft=lambda **kwargs: calls.append(kwargs) or {"id": "draft-1"},
        download_attachment=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not download")),
    )
    monkeypatch.setattr(attachment_support, "get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(attachment_support, "effective_dry_run", lambda: False)
    monkeypatch.setattr(attachment_support.settings, "max_attachment_bytes", 5)

    attachments = ({
        "filename": "big.pdf",
        "mime_type": "application/pdf",
        "size": 999,
        "attachment_id": "att-1",
    },)

    with _original_message_context("msg-1", attachments):
        with reply_to("alice@example.com"):
            result = draft_tools.create_draft.invoke({
                "subject": "Re",
                "content": "Body",
                "include_attachments": True,
            })

    assert "attachments" not in calls[0]
    assert "size limit" in result


def test_create_draft_include_attachments_in_dry_run_skips_download(monkeypatch):
    def _download(*_a, **_k):
        raise AssertionError("download_attachment must not be called in dry-run")

    provider = patch_provider(
        monkeypatch,
        draft_tools,
        create_draft=lambda **kwargs: {"dry_run": True, "action": "create_draft"},
        download_attachment=_download,
    )
    monkeypatch.setattr(attachment_support, "get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(attachment_support, "effective_dry_run", lambda: True)

    attachments = ({"filename": "invoice.pdf", "attachment_id": "att-1", "size": 10},)

    with _original_message_context("msg-1", attachments):
        with reply_to("alice@example.com"):
            result = draft_tools.create_draft.invoke({
                "subject": "Re",
                "content": "Body",
                "include_attachments": True,
            })

    assert "simulated" in result
