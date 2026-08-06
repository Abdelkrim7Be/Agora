from __future__ import annotations

import uuid

from types import SimpleNamespace

from conftest import ai_tool_call, patch_provider

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


def test_prompt_memory_is_bounded(monkeypatch):
    import src.graph as g

    monkeypatch.setattr(g.settings, "memory_prompt_max_chars", 10)

    assert g._prompt_memory("1234567890abcdef") == "1234567890\n[truncated]"


def test_respond_email_routes_to_agent(fake_llms, respond_email):
    """A 'respond' classification hands off to the response agent."""
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("Done", {"done": True})],
    )
    result = email_assistant.invoke({"email_input": respond_email}, _cfg())
    assert result["classification_decision"] == "respond"


def test_notify_workflow_routes_to_notify_approval(monkeypatch, respond_email):
    import src.graph as g
    from src.categories import CategoriesConfig

    cfg = CategoriesConfig(
        enabled=True,
        categories=[
            {
                "name": "reclamation",
                "display_name": "Réclamation",
                "priority": "urgent",
                "policy": "notify",
                "owner": "Operations",
                "approver": "zinebbellagnech@gmail.com",
                "route_to": ["zinebbellagnech@gmail.com"],
                "when": {"subject_contains": ["question"]},
            }
        ],
    )
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: cfg)
    email = {**respond_email, "email_id": "msg-route"}

    result = email_assistant.invoke({"email_input": email}, _cfg())

    assert result["classification_decision"] == "notify"
    assert result["workflow_owner"] == "Operations"
    assert result["workflow_route_to"] == ["zinebbellagnech@gmail.com"]
    request = result["__interrupt__"][0].value[0]
    assert request["action_request"]["action"] == "notify_internal"
    # The recipient is no longer an argument the model can set; tool_node
    # resolves it and exposes it read-only so the approver still sees it.
    assert "to" not in request["action_request"]["args"]
    assert request["action_request"]["recipients"] == ["zinebbellagnech@gmail.com"]
    assert "Réclamation" in request["action_request"]["args"]["note"]


def test_notify_workflow_resolves_role_directory(monkeypatch, respond_email):
    import src.graph as g
    from src.categories import CategoriesConfig
    from src.roles import ResolvedRole

    cfg = CategoriesConfig(
        enabled=True,
        categories=[
            {
                "name": "finance_review",
                "display_name": "Finance review",
                "priority": "urgent",
                "policy": "notify",
                "owner": "Finance",
                "approver": "finance.manager@example.com",
                "route_to": ["finance"],
                "when": {"subject_contains": ["question"]},
            }
        ],
    )
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: cfg)
    monkeypatch.setattr(
        g,
        "resolve_role",
        lambda key: ResolvedRole(
            role_key="finance",
            display_name="Finance",
            dept="Finance",
            emails=["finance@example.com", "backup@example.com"],
        ) if key.strip().lower() == "finance" else None,
    )
    email = {**respond_email, "email_id": "msg-role-route"}

    result = email_assistant.invoke({"email_input": email}, _cfg())

    request = result["__interrupt__"][0].value[0]
    assert "to" not in request["action_request"]["args"]
    assert request["action_request"]["recipients"] == ["finance@example.com", "backup@example.com"]
    assert result["workflow_route_to"] == ["finance"]


def test_notify_workflow_fan_out_approval_notifies_all_recipients(monkeypatch, fake_llms, respond_email):
    """A 2-recipient route_to produces ONE approval; approving it notifies both."""
    # The run continues past the notify approval into the agent loop, so the
    # drafting model must be stubbed too or the test reaches the network.
    fake_llms(classification="notify", tool_sequence=[ai_tool_call("Done", {"done": True})])
    import src.graph as g
    from src.categories import CategoriesConfig
    from langgraph.types import Command

    cfg = CategoriesConfig(
        enabled=True,
        categories=[
            {
                "name": "reclamation",
                "display_name": "Réclamation",
                "priority": "urgent",
                "policy": "notify",
                "owner": "Operations",
                "route_to": ["ops@example.com", "quality@example.com"],
                "when": {"subject_contains": ["question"]},
            }
        ],
    )
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: cfg)
    email = {**respond_email, "email_id": "msg-fanout"}
    run_cfg = _cfg()

    paused = email_assistant.invoke({"email_input": email}, run_cfg)
    request = paused["__interrupt__"][0].value[0]
    assert request["action_request"]["action"] == "notify_internal"
    assert "to" not in request["action_request"]["args"]
    assert request["action_request"]["recipients"] == ["ops@example.com", "quality@example.com"]

    sent_to = []
    from src.capabilities import email_tools

    patch_provider(
        monkeypatch,
        email_tools,
        notify_internal_message=lambda to, subject, note: sent_to.append(to) or {"id": "sent-notify"},
    )
    monkeypatch.setattr(email_tools.settings, "dry_run", False)

    done = email_assistant.invoke(Command(resume={"type": "approve", "args": None}), run_cfg)

    assert "__interrupt__" not in done
    # One notification, addressed to both recipients — not one send per recipient.
    assert sent_to == [["ops@example.com", "quality@example.com"]]


def test_notify_manual_workflow_routes_without_trusted_email_id(monkeypatch, respond_email):
    """notify_internal never re-fetches the original Gmail message, so unlike the old
    forward-based routing it works fine for manual (non-Gmail-sourced) runs too."""
    import src.graph as g
    from src.categories import CategoriesConfig

    cfg = CategoriesConfig(
        enabled=True,
        categories=[
            {
                "name": "reclamation",
                "display_name": "Réclamation",
                "priority": "urgent",
                "policy": "notify",
                "owner": "Operations",
                "route_to": ["ops@example.com"],
                "when": {"subject_contains": ["question"]},
            }
        ],
    )
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: cfg)

    result = email_assistant.invoke({"email_input": respond_email}, _cfg())

    assert result["classification_decision"] == "notify"
    assert not result.get("email_send_failed")
    request = result["__interrupt__"][0].value[0]
    assert request["action_request"]["action"] == "notify_internal"
    assert "to" not in request["action_request"]["args"]
    assert request["action_request"]["recipients"] == ["ops@example.com"]


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
    from src.categories import CategoriesConfig

    g, inbox_tools = _enable_auto_organize(monkeypatch)
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: CategoriesConfig(enabled=False))
    monkeypatch.setattr(g.settings, "security_enabled", False)
    fake_llms(classification="ignore", tool_sequence=[ai_tool_call("Done", {"done": True})])

    calls: list[tuple] = []

    def _modify(message_id, **kwargs):
        calls.append(("modify_labels", message_id, kwargs))
        return {"id": message_id}

    patch_provider(
        monkeypatch,
        inbox_tools,
        ensure_label=lambda label: calls.append(("ensure_label", label)) or "Label_auto",
        modify_labels=_modify,
        archive_message=lambda message_id: calls.append(("archive_message", message_id))
        or {"id": message_id},
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
    from src.categories import CategoriesConfig

    g, inbox_tools = _enable_auto_organize(monkeypatch, label="Auto/Skip")
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: CategoriesConfig(enabled=False))
    g._authorization_cache.clear()
    monkeypatch.setattr(g.settings, "security_enabled", True)
    fake_llms(classification="ignore", tool_sequence=[ai_tool_call("Done", {"done": True})])

    patch_provider(
        monkeypatch,
        inbox_tools,
        ensure_label=lambda label: "Label_auto",
        modify_labels=lambda *a, **k: {"id": a[0]},
        archive_message=lambda message_id: {"id": message_id},
    )

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
    patch_provider(
        monkeypatch,
        inbox_tools,
        ensure_label=lambda label: "Label_x",
        modify_labels=lambda message_id, **k: applied.append((message_id, k))
        or {"id": message_id},
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
    patch_provider(
        monkeypatch,
        inbox_tools,
        ensure_label=lambda label: "Label_x",
        modify_labels=lambda message_id, **k: {"id": message_id},
    )

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
    from src.categories import CategoriesConfig
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: CategoriesConfig(enabled=False))
    monkeypatch.setattr(g, "llm_with_tools", _CaptureToolLLM())
    store = InMemoryStore()
    store.put(namespace("writing_style"), "user_preferences", wrap_preferences("Use a warm concise voice."))
    graph = g.overall_workflow.compile(checkpointer=MemorySaver(), store=store)

    graph.invoke({"email_input": respond_email}, _cfg())

    assert "< Writing Style >" in captured["system"]
    assert "Use a warm concise voice." in captured["system"]
    assert "< Response Preferences >" in captured["system"]


def test_triage_attaches_category_metadata(monkeypatch, fake_llms, respond_email):
    import src.graph as g
    from src.categories import CategoriesConfig

    cfg = CategoriesConfig(
        enabled=True,
        categories=[
            {
                "name": "support",
                "display_name": "Support",
                "priority": "urgent",
                "policy": "notify",
                "owner": "Support team",
                "approver": "support.manager@example.com",
                "route_to": ["support@example.com"],
                "when": {"sender_domain": ["example.com"]},
            }
        ],
    )
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: cfg)
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("Done", {"done": True})],
    )

    result = email_assistant.invoke({"email_input": respond_email}, _cfg())

    assert result["category"] == "support"
    assert result["category_display_name"] == "Support"
    assert result["priority"] == "urgent"
    assert result["workflow_owner"] == "Support team"
    assert result["workflow_approver"] == "support.manager@example.com"
    assert result["workflow_route_to"] == ["support@example.com"]


def test_triage_cache_reuses_repeated_sender_subject_decision(monkeypatch):
    import src.graph as g
    from src.categories import CategoriesConfig

    cache = {}
    calls = []

    class _CountingRouter:
        def invoke(self, _messages, config=None):
            calls.append(config)
            return SimpleNamespace(classification="notify", category=None)

    monkeypatch.setattr(g, "llm_router", _CountingRouter())
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: CategoriesConfig(enabled=False))
    monkeypatch.setattr(g.settings, "triage_cache_ttl_seconds", 60)
    monkeypatch.setattr(g, "cache_get_json", lambda key: cache.get(key))
    monkeypatch.setattr(
        g,
        "cache_set_json",
        lambda key, value, ttl_seconds: cache.setdefault(key, value) is value,
    )

    first = {
        "author": "Digest <updates@example.com>",
        "to": "Me <me@example.com>",
        "subject": "Daily report 123",
        "email_thread": "Here is today's report.",
    }
    second = {
        **first,
        "subject": "Daily report 456",
        "email_thread": "A different body should not matter for this cache key.",
    }

    assert email_assistant.invoke({"email_input": first}, _cfg())["classification_decision"] == "notify"
    assert email_assistant.invoke({"email_input": second}, _cfg())["classification_decision"] == "notify"
    assert len(calls) == 1


def test_auto_draft_category_routes_to_pending_approval(monkeypatch, fake_llms, respond_email):
    import src.graph as g
    from src.categories import CategoriesConfig

    cfg = CategoriesConfig(
        enabled=True,
        categories=[
            {
                "name": "reclamation",
                "display_name": "Reclamation",
                "priority": "urgent",
                "policy": "auto_draft",
                "template": "complaint_reply",
                "owner": "Support team",
                "approver": "support.manager@example.com",
                "route_to": ["support@example.com"],
                "when": {"sender_domain": ["example.com"]},
            }
        ],
        templates=[
            {
                "name": "complaint_reply",
                "subject": "Re: {{subject}}",
                "body": "Thanks for the context. I will look into this.",
            }
        ],
    )
    monkeypatch.setattr(g, "load_categories", lambda *a, **kw: cfg)
    fake_llms(classification="notify")

    result = email_assistant.invoke({"email_input": respond_email}, _cfg())

    assert result["classification_decision"] == "respond"
    assert result["category"] == "reclamation"
    assert result["priority"] == "urgent"
    assert result["workflow_owner"] == "Support team"
    assert result["workflow_approver"] == "support.manager@example.com"
    assert result["workflow_route_to"] == ["support@example.com"]
    request = result["__interrupt__"][0].value[0]
    assert request["action_request"]["action"] == "write_email"
    assert request["action_request"]["args"]["subject"] == "Re: Quick question about the API"
