from __future__ import annotations

import uuid

from conftest import ai_tool_call

from src.graph import email_assistant
from src.utils import extract_tool_call_names


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


def test_respond_email_routes_to_agent(fake_llms, respond_email):
    """A 'respond' classification hands off to the response agent."""
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("Done", {"done": True})],
    )
    result = email_assistant.invoke({"email_input": respond_email}, _cfg())
    assert result["classification_decision"] == "respond"


def test_ignore_email_ends_after_triage(fake_llms, ignore_email):
    """An 'ignore' classification stops at triage without drafting."""
    fake_llms(classification="ignore")
    result = email_assistant.invoke({"email_input": ignore_email}, _cfg())
    assert result["classification_decision"] == "ignore"
    assert "write_email" not in extract_tool_call_names(result.get("messages", []))
