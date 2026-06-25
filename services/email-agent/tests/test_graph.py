from __future__ import annotations

import uuid

from conftest import ai_tool_call

from src.graph import _recover_tool_call_from_failed_generation, email_assistant
from src.config import AutoOrganizeConfig
from src.utils import extract_tool_call_names


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


class _GroqToolUseError(Exception):
    body = {
        "error": {
            "failed_generation": (
                '<function=write_email {"to": "alice@example.com", '
                '"subject": "Re: question", "content": "Here you go."}</function>'
            )
        }
    }


def test_recovers_groq_failed_write_email_tool_call():
    message = _recover_tool_call_from_failed_generation(_GroqToolUseError())

    assert message is not None
    assert message.tool_calls[0]["name"] == "write_email"
    assert message.tool_calls[0]["args"] == {
        "to": "alice@example.com",
        "subject": "Re: question",
        "content": "Here you go.",
    }


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


def _enable_auto_organize(monkeypatch, label: str = "Auto/Ignored"):
    import src.graph as g
    from src.capabilities import inbox_tools

    monkeypatch.setattr(
        g.config,
        "auto_organize",
        AutoOrganizeConfig(enabled=True, ignored_label=label),
    )
    monkeypatch.setitem(g.config.capabilities, "inbox", True)
    monkeypatch.setitem(g.tools_by_name_map, "apply_label", inbox_tools.apply_label)
    monkeypatch.setitem(g.tools_by_name_map, "archive_email", inbox_tools.archive_email)
    return g, inbox_tools


def test_ignore_email_auto_organizes_when_enabled(monkeypatch, fake_llms, ignore_email):
    g, inbox_tools = _enable_auto_organize(monkeypatch)
    monkeypatch.setattr(g.settings, "security_enabled", False)
    fake_llms(classification="ignore", tool_sequence=[ai_tool_call("Done", {"done": True})])

    calls: list[tuple] = []
    monkeypatch.setattr(
        inbox_tools,
        "ensure_label",
        lambda label: calls.append(("ensure_label", label)) or "Label_auto",
    )

    def _modify(message_id, **kwargs):
        calls.append(("modify_labels", message_id, kwargs))
        return {"id": message_id}

    monkeypatch.setattr(inbox_tools, "modify_labels", _modify)
    monkeypatch.setattr(
        inbox_tools,
        "archive_message",
        lambda message_id: calls.append(("archive_message", message_id)) or {"id": message_id},
    )

    email = {**ignore_email, "email_id": "msg-auto"}

    result = email_assistant.invoke({"email_input": email}, _cfg())

    assert result["classification_decision"] == "ignore"
    assert result["auto_organized"] is True
    assert calls == [
        ("ensure_label", "Auto/Ignored"),
        ("modify_labels", "msg-auto", {"add_label_ids": ["Label_auto"]}),
        ("archive_message", "msg-auto"),
    ]
    assert "Done" not in extract_tool_call_names(result.get("messages", []))


def test_auto_organize_uses_authorization_when_security_enabled(
    monkeypatch, fake_llms, ignore_email
):
    g, inbox_tools = _enable_auto_organize(monkeypatch, label="Auto/Skip")
    g._authorization_cache.clear()
    monkeypatch.setattr(g.settings, "security_enabled", True)
    fake_llms(classification="ignore", tool_sequence=[ai_tool_call("Done", {"done": True})])

    monkeypatch.setattr(inbox_tools, "ensure_label", lambda label: "Label_auto")
    monkeypatch.setattr(inbox_tools, "modify_labels", lambda *a, **k: {"id": a[0]})
    monkeypatch.setattr(inbox_tools, "archive_message", lambda message_id: {"id": message_id})

    authz_calls: list[dict] = []

    def _fake_authorize(action: str, args: dict, run_id: str, action_id: str = ""):
        authz_calls.append({
            "action": action,
            "args": args,
            "run_id": run_id,
            "action_id": action_id,
        })
        return {"decision": "allow", "reason": "test policy"}

    monkeypatch.setattr(g, "authorize_action", _fake_authorize)
    email = {**ignore_email, "email_id": "msg-auto-sec"}
    cfg = {"configurable": {"thread_id": "run-auto-sec"}}

    result = email_assistant.invoke({"email_input": email}, cfg)

    assert result["auto_organized"] is True
    assert authz_calls == [
        {
            "action": "apply_label",
            "args": {"label": "Auto/Skip"},
            "run_id": "run-auto-sec",
            "action_id": "auto_apply_ignored_label",
        },
        {
            "action": "archive_email",
            "args": {},
            "run_id": "run-auto-sec",
            "action_id": "auto_archive_ignored",
        },
    ]


def test_automation_label_only_plan_runs_without_notify(monkeypatch):
    """A label-only automation plan executes via tool_node, ends the run, and does
    NOT force a 'notify' classification (so it won't pollute the daily digest)."""
    import src.graph as g
    from src.capabilities import inbox_tools

    monkeypatch.setattr(g.settings, "security_enabled", False)
    monkeypatch.setitem(g.tools_by_name_map, "apply_label", inbox_tools.apply_label)
    applied: list[tuple] = []
    monkeypatch.setattr(inbox_tools, "ensure_label", lambda label: "Label_x")
    monkeypatch.setattr(
        inbox_tools,
        "modify_labels",
        lambda message_id, **k: applied.append((message_id, k)) or {"id": message_id},
    )

    email = {
        "author": "a@example.com", "to": "me@example.com", "subject": "Hi",
        "email_thread": "body", "email_id": "msg-rule",
        "automation": {
            "matched_rules": ["x"],
            "tool_calls": [
                {"name": "apply_label", "args": {"label": "Clients"}, "id": "r0", "type": "tool_call"}
            ],
            "terminal_status": None,
        },
    }
    result = email_assistant.invoke({"email_input": email}, _cfg())

    assert result.get("automation_acted") is True
    assert applied == [("msg-rule", {"add_label_ids": ["Label_x"]})]
    assert result.get("classification_decision") is None


def test_automation_notify_rule_tags_classification(monkeypatch):
    """A rule whose plan sets terminal_status='notify' tags the run as notify."""
    import src.graph as g
    from src.capabilities import inbox_tools

    monkeypatch.setattr(g.settings, "security_enabled", False)
    monkeypatch.setitem(g.tools_by_name_map, "apply_label", inbox_tools.apply_label)
    monkeypatch.setattr(inbox_tools, "ensure_label", lambda label: "Label_x")
    monkeypatch.setattr(inbox_tools, "modify_labels", lambda message_id, **k: {"id": message_id})

    email = {
        "author": "a@example.com", "to": "me@example.com", "subject": "Hi",
        "email_thread": "body", "email_id": "msg-rule",
        "automation": {
            "matched_rules": ["x"],
            "tool_calls": [
                {"name": "apply_label", "args": {"label": "Clients"}, "id": "r0", "type": "tool_call"}
            ],
            "terminal_status": "notify",
        },
    }
    result = email_assistant.invoke({"email_input": email}, _cfg())

    assert result.get("automation_acted") is True
    assert result.get("classification_decision") == "notify"


def test_llm_call_includes_writing_style_in_prompt(monkeypatch, fake_llms, respond_email):
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.store.memory import InMemoryStore
    import src.graph as g
    from src.memory import namespace, wrap_preferences

    captured = {}

    class _CaptureToolLLM:
        def invoke(self, messages, config=None):
            captured["system"] = messages[0]["content"]
            return AIMessage(
                content="",
                tool_calls=[{"name": "Done", "args": {"done": True}, "id": "done-1", "type": "tool_call"}],
            )

    fake_llms(classification="respond")
    monkeypatch.setattr(g, "llm_with_tools", _CaptureToolLLM())
    store = InMemoryStore()
    store.put(namespace("writing_style"), "user_preferences", wrap_preferences("Use a warm concise voice."))
    graph = g.overall_workflow.compile(checkpointer=MemorySaver(), store=store)

    graph.invoke({"email_input": respond_email}, _cfg())

    assert "< Writing Style >" in captured["system"]
    assert "Use a warm concise voice." in captured["system"]
    assert "< Response Preferences >" in captured["system"]
