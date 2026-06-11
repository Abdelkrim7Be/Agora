from __future__ import annotations

import uuid

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from src.graph import _parse_decision, email_assistant, overall_workflow
from tests.conftest import RESPOND_EMAIL, ai_tool_call


DRAFT = {"to": "alice@example.com", "subject": "Re: question", "content": "Here you go."}
DRAFT2 = {"to": "alice@example.com", "subject": "Re: question", "content": "Shorter version."}
EDITED = {"to": "alice@example.com", "subject": "Re: question", "content": "Actually, here is more detail."}


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


# --- _parse_decision unit tests ---

def test_parse_decision_list_accept():
    assert _parse_decision([{"type": "accept"}]) == ("accept", None)


def test_parse_decision_dict_approve_no_args_maps_to_accept():
    assert _parse_decision({"type": "approve", "args": None}) == ("accept", None)


def test_parse_decision_dict_reject_maps_to_ignore():
    assert _parse_decision({"type": "reject"}) == ("ignore", None)


def test_parse_decision_list_ignore():
    assert _parse_decision([{"type": "ignore"}]) == ("ignore", None)


def test_parse_decision_rest_approve_with_args_maps_to_edit():
    type_, data = _parse_decision({"type": "approve", "args": EDITED})
    assert type_ == "edit"
    assert data == EDITED


def test_parse_decision_agent_inbox_edit_unnests_args():
    raw = [{"type": "edit", "args": {"action": "write_email", "args": EDITED}}]
    type_, data = _parse_decision(raw)
    assert type_ == "edit"
    assert data == EDITED


def test_parse_decision_response_passes_feedback_string():
    type_, data = _parse_decision([{"type": "response", "args": "make it shorter"}])
    assert type_ == "response"
    assert data == "make it shorter"


def test_parse_decision_missing_type_fails_closed():
    # An unparseable approval must never fall through to a send.
    with pytest.raises(ValueError):
        _parse_decision({})


def test_parse_decision_unknown_type_fails_closed():
    with pytest.raises(ValueError):
        _parse_decision([{"type": "bogus"}])


# --- Interrupt payload shape ---

def test_interrupt_payload_has_agent_inbox_schema(fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    cfg = _cfg()
    paused = email_assistant.invoke({"email_input": respond_email}, cfg)

    payload = paused["__interrupt__"][0].value
    assert isinstance(payload, list), "Agent Inbox expects a list"
    request = payload[0]
    assert request["action_request"]["action"] == "write_email"
    assert request["action_request"]["args"] == DRAFT
    assert "description" in request
    assert request["config"]["allow_accept"] is True
    assert request["config"]["allow_edit"] is True
    assert request["config"]["allow_respond"] is True
    assert request["config"]["allow_ignore"] is True


def test_interrupt_description_includes_draft_content(fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    cfg = _cfg()
    paused = email_assistant.invoke({"email_input": respond_email}, cfg)
    description = paused["__interrupt__"][0].value[0]["description"]
    assert "alice@example.com" in description
    assert "Here you go." in description


# --- Agent Inbox resume routing through the real graph (LLMs faked) ---

def test_accept_sends_email(fake_llms, respond_email):
    """Agent Inbox 'accept' executes write_email and completes the run."""
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    cfg = _cfg()
    email_assistant.invoke({"email_input": respond_email}, cfg)
    result = email_assistant.invoke(Command(resume=[{"type": "accept"}]), cfg)

    assert "__interrupt__" not in result
    assert any(
        "Email sent to" in (getattr(m, "content", "") or "")
        for m in result["messages"]
    )


def test_edit_sends_with_edited_args_and_updates_memory(fake_llms, respond_email):
    """Agent Inbox 'edit' executes with edited args and teaches response_preferences."""
    store = InMemoryStore()
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    graph = overall_workflow.compile(checkpointer=MemorySaver(), store=store)
    cfg = _cfg()

    graph.invoke({"email_input": respond_email}, cfg)
    result = graph.invoke(
        Command(resume=[{"type": "edit", "args": {"action": "write_email", "args": EDITED}}]),
        cfg,
    )

    assert "__interrupt__" not in result
    assert any(
        "Email sent to" in (getattr(m, "content", "") or "")
        for m in result["messages"]
    )
    item = store.get(("email_agent", "default", "response_preferences"), "user_preferences")
    assert item is not None


def test_ignore_does_not_send_and_updates_triage_memory(fake_llms, respond_email):
    """Agent Inbox 'ignore' skips send and teaches triage_preferences."""
    store = InMemoryStore()
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    graph = overall_workflow.compile(checkpointer=MemorySaver(), store=store)
    cfg = _cfg()

    graph.invoke({"email_input": respond_email}, cfg)
    result = graph.invoke(Command(resume=[{"type": "ignore"}]), cfg)

    assert "__interrupt__" not in result
    assert not any(
        "Email sent to" in (getattr(m, "content", "") or "")
        for m in result["messages"]
    )
    item = store.get(("email_agent", "default", "triage_preferences"), "user_preferences")
    assert item is not None


def test_response_feedback_loops_back_and_triggers_redraft(fake_llms, respond_email):
    """Agent Inbox 'response' (free-text feedback) loops back to llm_call for re-drafting."""
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),   # first draft — interrupted
            ai_tool_call("write_email", DRAFT2, "c2"),  # re-draft after feedback
            ai_tool_call("Done", {"done": True}, "c3"),
        ],
    )
    cfg = _cfg()

    # First run: pauses at draft1
    paused1 = email_assistant.invoke({"email_input": respond_email}, cfg)
    assert paused1["__interrupt__"]

    # Feedback: should NOT send, should loop back and pause again on draft2
    paused2 = email_assistant.invoke(
        Command(resume=[{"type": "response", "args": "make it shorter"}]), cfg
    )
    assert paused2["__interrupt__"], "expected a second interrupt after feedback"
    request2 = paused2["__interrupt__"][0].value[0]
    assert request2["action_request"]["action"] == "write_email"
    assert request2["action_request"]["args"] == DRAFT2
