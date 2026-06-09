from __future__ import annotations

from src.graph import email_assistant
from src.utils import extract_tool_call_names


def test_respond_email_routes_to_agent_and_drafts(respond_email):
    """A genuine question should be classified 'respond' and produce a write_email call."""
    result = email_assistant.invoke({"email_input": respond_email})

    assert result["classification_decision"] == "respond"
    tool_calls = extract_tool_call_names(result["messages"])
    assert "write_email" in tool_calls


def test_ignore_email_ends_after_triage(ignore_email):
    """A promotional email should be classified 'ignore' and skip the response agent."""
    result = email_assistant.invoke({"email_input": ignore_email})

    assert result["classification_decision"] == "ignore"
    # No response was drafted.
    assert "write_email" not in extract_tool_call_names(result.get("messages", []))
