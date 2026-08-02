from __future__ import annotations

from contextlib import contextmanager

from langgraph.store.memory import InMemoryStore

from src.capabilities import current_gmail_thread_id
from src.capabilities import draft_tools
from tests.conftest import ai_tool_call, patch_provider


@contextmanager
def _thread_context(thread_id: str | None):
    token = current_gmail_thread_id.set(thread_id)
    try:
        yield
    finally:
        current_gmail_thread_id.reset(token)


def test_create_draft_uses_trusted_thread_context(monkeypatch):
    calls: list[dict] = []

    def _create(**kwargs):
        calls.append(kwargs)
        return {"id": "draft-1"}

    patch_provider(monkeypatch, draft_tools, create_draft=_create)

    with _thread_context("thread-1"):
        result = draft_tools.create_draft.invoke({
            "to": "alice@example.com",
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
        result = draft_tools.create_draft.invoke({
            "to": "alice@example.com",
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
    assert set(schema["properties"]) == {"to", "subject", "content"}


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
        "email_input": {"gmail_thread_id": "thread-context"},
        "messages": [
            ai_tool_call(
                "create_draft",
                {"to": "a@example.com", "subject": "Re", "content": "Body"},
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
