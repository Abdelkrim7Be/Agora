from __future__ import annotations

import pytest
from langgraph.store.memory import InMemoryStore

from src.memory import UserPreferences, get_memory, namespace, update_memory
from src.tenant import user_context
from tests.conftest import _FakeMemoryLLM


# ---------------------------------------------------------------------------
# get_memory
# ---------------------------------------------------------------------------

def test_get_memory_seeds_from_default_on_first_call():
    store = InMemoryStore()
    ns = namespace("triage_preferences")
    default = "default triage rules"
    result = get_memory(store, ns, default)
    assert result == default


def test_get_memory_writes_default_to_store_on_first_call():
    store = InMemoryStore()
    ns = namespace("triage_preferences")
    get_memory(store, ns, "seed value")
    item = store.get(ns, "user_preferences")
    assert item is not None
    assert item.value == {"preferences": "seed value"}


def test_get_memory_returns_stored_value_after_put():
    store = InMemoryStore()
    ns = namespace("response_preferences")
    store.put(ns, "user_preferences", "already stored")
    result = get_memory(store, ns, "ignored default")
    assert result == "already stored"


def test_get_memory_does_not_overwrite_existing_value():
    store = InMemoryStore()
    ns = namespace("triage_preferences")
    store.put(ns, "user_preferences", "original")
    get_memory(store, ns, "new default")
    assert store.get(ns, "user_preferences").value == "original"


# ---------------------------------------------------------------------------
# update_memory
# ---------------------------------------------------------------------------

def test_update_memory_writes_new_preferences():
    store = InMemoryStore()
    ns = namespace("response_preferences")
    store.put(ns, "user_preferences", "original preferences")
    llm = _FakeMemoryLLM("updated preferences")
    update_memory(store, ns, [{"role": "user", "content": "feedback"}], llm)
    assert store.get(ns, "user_preferences").value == {"preferences": "updated preferences"}


def test_update_memory_works_with_empty_store():
    store = InMemoryStore()
    ns = namespace("triage_preferences")
    llm = _FakeMemoryLLM("fresh preferences")
    update_memory(store, ns, [{"role": "user", "content": "feedback"}], llm)
    assert store.get(ns, "user_preferences").value == {"preferences": "fresh preferences"}


# ---------------------------------------------------------------------------
# namespace
# ---------------------------------------------------------------------------

def test_namespace_structure():
    ns = namespace("triage_preferences")
    assert ns == ("email_agent", "default", "triage_preferences")

    ns2 = namespace("response_preferences")
    assert ns2 == ("email_agent", "default", "response_preferences")


def test_namespace_uses_current_user_context():
    with user_context("alice@example.com"):
        assert namespace("triage_preferences") == (
            "email_agent",
            "alice@example.com",
            "triage_preferences",
        )


# ---------------------------------------------------------------------------
# Graph-level: reject triggers triage_preferences update
# ---------------------------------------------------------------------------

def test_reject_updates_triage_preferences(fake_llms):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.store.memory import InMemoryStore
    from langgraph.types import Command
    from src.graph import overall_workflow
    from tests.conftest import ai_tool_call, RESPOND_EMAIL

    memory_store = InMemoryStore()
    graph = overall_workflow.compile(
        checkpointer=MemorySaver(), store=memory_store
    )

    # After rejection the graph routes back to llm_call — provide Done so the run ends.
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", {"to": "a@b.com", "subject": "Re", "content": "Hi"}, call_id="c1"),
            ai_tool_call("Done", {"done": True}, call_id="c2"),
        ],
        memory_preference="do not respond to API questions",
    )

    run_id = "test-reject-001"
    cfg = {"configurable": {"thread_id": run_id}}

    result = graph.invoke({"email_input": RESPOND_EMAIL}, cfg)
    assert result.get("__interrupt__"), "expected interrupt on write_email"

    graph.invoke(Command(resume={"type": "reject"}), cfg)

    item = memory_store.get(namespace("triage_preferences"), "user_preferences")
    assert item is not None
    assert item.value == {"preferences": "do not respond to API questions"}


# ---------------------------------------------------------------------------
# Graph-level: the reject memory update never sends a dangling tool_call.
# An assistant message with tool_calls must be answered by a tool message for
# each id, or Groq rejects the request. This guards that contract offline.
# ---------------------------------------------------------------------------

def _assert_no_dangling_tool_calls(messages):
    answered = set()
    requested = []
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "tool":
            answered.add(m.get("tool_call_id"))
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            requested.extend(tc["id"] for tc in tool_calls)
    dangling = [rid for rid in requested if rid not in answered]
    assert not dangling, f"unanswered tool_calls in memory messages: {dangling}"


def test_reject_memory_messages_have_no_dangling_tool_call(fake_llms, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.store.memory import InMemoryStore
    from langgraph.types import Command
    import src.graph as g
    from tests.conftest import ai_tool_call, RESPOND_EMAIL

    captured = {}
    real_update = g.update_memory

    def spy(store, ns, messages, llm):
        captured["messages"] = messages
        return real_update(store, ns, messages, llm)

    monkeypatch.setattr(g, "update_memory", spy)

    graph = g.overall_workflow.compile(
        checkpointer=MemorySaver(), store=InMemoryStore()
    )
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", {"to": "a@b.com", "subject": "Re", "content": "Hi"}, call_id="c1"),
            ai_tool_call("Done", {"done": True}, call_id="c2"),
        ],
    )

    cfg = {"configurable": {"thread_id": "test-dangling-001"}}
    graph.invoke({"email_input": RESPOND_EMAIL}, cfg)
    graph.invoke(Command(resume={"type": "reject"}), cfg)

    assert "messages" in captured, "update_memory was not called on reject"
    _assert_no_dangling_tool_calls(captured["messages"])


# ---------------------------------------------------------------------------
# Graph-level: edit triggers response_preferences update
# ---------------------------------------------------------------------------

def test_edit_updates_response_preferences(fake_llms):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.store.memory import InMemoryStore
    from langgraph.types import Command
    from src.graph import overall_workflow
    from tests.conftest import ai_tool_call, RESPOND_EMAIL

    memory_store = InMemoryStore()
    graph = overall_workflow.compile(
        checkpointer=MemorySaver(), store=memory_store
    )

    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", {"to": "a@b.com", "subject": "Re", "content": "Hi"}, call_id="c1"),
        ],
        memory_preference="be more concise in replies",
    )

    run_id = "test-edit-001"
    cfg = {"configurable": {"thread_id": run_id}}

    result = graph.invoke({"email_input": RESPOND_EMAIL}, cfg)
    assert result.get("__interrupt__"), "expected interrupt on write_email"

    edited = {"to": "a@b.com", "subject": "Re", "content": "Sure, will do."}
    graph.invoke(Command(resume={"type": "approve", "args": edited}), cfg)

    item = memory_store.get(namespace("response_preferences"), "user_preferences")
    assert item is not None
    assert item.value == {"preferences": "be more concise in replies"}


# ---------------------------------------------------------------------------
# Graph-level: plain approve does NOT call update_memory
# Pre-seed both namespaces, assert values are unchanged after plain approve.
# ---------------------------------------------------------------------------

def test_plain_approve_does_not_update_memory(fake_llms):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.store.memory import InMemoryStore
    from langgraph.types import Command
    from src.graph import overall_workflow
    from tests.conftest import ai_tool_call, RESPOND_EMAIL

    memory_store = InMemoryStore()

    sentinel_triage = "pre-seeded triage rules"
    sentinel_response = "pre-seeded response rules"
    memory_store.put(namespace("triage_preferences"), "user_preferences", sentinel_triage)
    memory_store.put(namespace("response_preferences"), "user_preferences", sentinel_response)

    graph = overall_workflow.compile(
        checkpointer=MemorySaver(), store=memory_store
    )

    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", {"to": "a@b.com", "subject": "Re", "content": "Hi"}, call_id="c1"),
        ],
        memory_preference="should not appear in store",
    )

    run_id = "test-approve-001"
    cfg = {"configurable": {"thread_id": run_id}}

    result = graph.invoke({"email_input": RESPOND_EMAIL}, cfg)
    assert result.get("__interrupt__")

    graph.invoke(Command(resume={"type": "approve", "args": None}), cfg)

    # Sentinel values must be unchanged — update_memory was never called.
    assert memory_store.get(namespace("triage_preferences"), "user_preferences").value == sentinel_triage
    assert memory_store.get(namespace("response_preferences"), "user_preferences").value == sentinel_response


def test_done_tool_accepts_stringified_argument():
    """Groq's llama sometimes emits {"done": "true"}; the schema must not reject it."""
    from src.capabilities.email_tools import Done

    assert Done.invoke({"done": "true"}) is not None
    assert Done.invoke({"done": True}) is not None
    assert Done.invoke({}) is not None  # optional


def test_memory_helpers_round_trip():
    from src.memory import preferences_text, wrap_preferences

    assert wrap_preferences("hello") == {"preferences": "hello"}
    assert preferences_text({"preferences": "hello"}) == "hello"
    assert preferences_text("legacy raw string") == "legacy raw string"  # sqlite back-compat
    assert preferences_text(None) == ""


def test_update_memory_skips_llm_failure_without_overwriting():
    class Store:
        def __init__(self):
            self.value = {"preferences": "existing"}
            self.put_calls = []

        def get(self, ns, key):
            return type("Item", (), {"value": self.value})()

        def put(self, ns, key, value):
            self.put_calls.append((ns, key, value))

    class FailingLlm:
        def invoke(self, messages):
            raise RuntimeError("network unavailable")

    store = Store()
    ns = ("email_agent", "owner", "response_preferences")

    update_memory(store, ns, [{"role": "user", "content": "feedback"}], FailingLlm())

    assert store.put_calls == []
