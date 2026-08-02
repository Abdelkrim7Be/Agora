from __future__ import annotations

from contextlib import contextmanager

import pytest
from langgraph.store.memory import InMemoryStore

from src.capabilities import current_email_id
from src.capabilities import inbox_tools
from tests.conftest import ai_tool_call, patch_provider


@contextmanager
def _email_context(message_id: str):
    token = current_email_id.set(message_id)
    try:
        yield
    finally:
        current_email_id.reset(token)


def test_inbox_tools_require_graph_email_context():
    with pytest.raises(RuntimeError, match="trusted email_id"):
        inbox_tools.archive_email.invoke({})


def test_apply_label_uses_context_email_id(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def _modify(message_id, **kwargs):
        calls.append((message_id, kwargs))
        return {"id": message_id}

    patch_provider(
        monkeypatch, inbox_tools, ensure_label=lambda label: "Label_123", modify_labels=_modify
    )

    with _email_context("msg-1"):
        result = inbox_tools.apply_label.invoke({"label": "Clients"})

    assert result == "Applied label 'Clients' to the current email."
    assert calls == [("msg-1", {"add_label_ids": ["Label_123"]})]


def test_remove_label_resolves_existing_label(monkeypatch):
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(inbox_tools.settings, "dry_run", False)

    def _modify(message_id, **kwargs):
        calls.append((message_id, kwargs))
        return {"id": message_id}

    patch_provider(
        monkeypatch,
        inbox_tools,
        list_labels=lambda: [{"id": "Label_123", "name": "Clients"}],
        modify_labels=_modify,
    )

    with _email_context("msg-2"):
        result = inbox_tools.remove_label.invoke({"label": "Clients"})

    assert result == "Removed label 'Clients' from the current email."
    assert calls == [("msg-2", {"remove_label_ids": ["Label_123"]})]


def test_remove_label_fails_when_label_is_missing(monkeypatch):
    monkeypatch.setattr(inbox_tools.settings, "dry_run", False)
    patch_provider(monkeypatch, inbox_tools, list_labels=lambda: [])

    with _email_context("msg-3"):
        with pytest.raises(ValueError, match="Gmail label not found"):
            inbox_tools.remove_label.invoke({"label": "Missing"})


def test_mark_read_and_unread_use_context_email_id(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def _modify(message_id, **kwargs):
        calls.append((message_id, kwargs))
        return {"id": message_id}

    patch_provider(monkeypatch, inbox_tools, modify_labels=_modify)

    with _email_context("msg-4"):
        assert inbox_tools.mark_read.invoke({}) == "Marked the current email as read."
        assert inbox_tools.mark_unread.invoke({}) == "Marked the current email as unread."

    assert calls == [
        ("msg-4", {"remove_label_ids": ["UNREAD"]}),
        ("msg-4", {"add_label_ids": ["UNREAD"]}),
    ]


def test_archive_and_trash_use_context_email_id(monkeypatch):
    archived: list[str] = []
    trashed: list[str] = []
    patch_provider(
        monkeypatch,
        inbox_tools,
        archive_message=lambda message_id: archived.append(message_id),
        trash_message=lambda message_id: trashed.append(message_id),
    )

    with _email_context("msg-5"):
        assert inbox_tools.archive_email.invoke({}) == "Archived the current email."
        assert inbox_tools.trash_email.invoke({}) == "Moved the current email to trash."

    assert archived == ["msg-5"]
    assert trashed == ["msg-5"]


def test_tool_node_injects_current_email_id_for_inbox_tools(monkeypatch):
    import src.graph as g

    archived: list[str] = []
    monkeypatch.setattr(g.settings, "security_enabled", False)
    monkeypatch.setitem(g.tools_by_name_map, "archive_email", inbox_tools.archive_email)
    patch_provider(
        monkeypatch, inbox_tools, archive_message=lambda message_id: archived.append(message_id)
    )

    state = {
        "email_input": {"email_id": "msg-context"},
        "messages": [ai_tool_call("archive_email", {}, "call-inbox")],
    }

    result = g.tool_node(state, InMemoryStore(), config={"configurable": {"thread_id": "run"}})

    assert archived == ["msg-context"]
    assert current_email_id.get() is None
    assert result["messages"] == [
        {
            "role": "tool",
            "content": "Archived the current email.",
            "tool_call_id": "call-inbox",
        }
    ]


def test_tool_node_handles_missing_email_id_gracefully(monkeypatch):
    # Inbox tool invoked without a trusted email_id (e.g. manual /run) must not crash
    # the run — it should surface a recoverable tool message instead.
    import src.graph as g

    monkeypatch.setattr(g.settings, "security_enabled", False)
    monkeypatch.setitem(g.tools_by_name_map, "archive_email", inbox_tools.archive_email)

    state = {
        "email_input": {},  # no email_id
        "messages": [ai_tool_call("archive_email", {}, "call-inbox")],
    }

    result = g.tool_node(state, InMemoryStore(), config={"configurable": {"thread_id": "run"}})

    assert current_email_id.get() is None
    message = result["messages"][0]
    assert message["tool_call_id"] == "call-inbox"
    assert "could not be completed" in message["content"]
    assert "Call Done" in message["content"]
