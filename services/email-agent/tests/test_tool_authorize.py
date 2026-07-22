from __future__ import annotations

from langgraph.types import Command

from tests.conftest import ai_tool_call
from src.graph import email_assistant

DRAFT = {"to": "alice@example.com", "subject": "Re: question", "content": "Here you go."}


def _cfg(run_id: str = "run-sec4") -> dict:
    return {"configurable": {"thread_id": run_id}}


def _install_authz(monkeypatch, decision: str, reason: str = "test policy") -> list[dict]:
    import src.graph as g

    calls: list[dict] = []
    g._authorization_cache.clear()
    monkeypatch.setattr(g.settings, "security_enabled", True)

    def _fake_authorize(
        action: str,
        args: dict,
        run_id: str,
        action_id: str = "",
    ) -> dict:
        calls.append({
            "action": action,
            "args": args,
            "run_id": run_id,
            "action_id": action_id,
        })
        return {"decision": decision, "reason": reason}

    monkeypatch.setattr(g, "authorize_action", _fake_authorize)
    monkeypatch.setattr(g, "audit_output", lambda *a, **k: {"flagged": False, "reasons": []})
    return calls


def _email_was_sent(messages) -> bool:
    return any("Email sent to" in (getattr(m, "content", "") or "") for m in messages)


def test_authorize_allow_executes_without_hitl(monkeypatch, fake_llms, respond_email):
    calls = _install_authz(monkeypatch, "allow")
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("write_email", DRAFT, "c1")],
    )

    result = email_assistant.invoke({"email_input": respond_email}, _cfg("run-allow"))

    assert "__interrupt__" not in result
    assert _email_was_sent(result["messages"])
    assert calls == [{
        "action": "write_email",
        "args": DRAFT,
        "run_id": "run-allow",
        "action_id": "c1",
    }]


def test_output_audit_blocks_flagged_content_after_authorize_allows(monkeypatch, fake_llms, respond_email):
    """/authorize allowing the action is not the last word — a flagged draft still
    never reaches the real Gmail send."""
    import src.graph as g

    _install_authz(monkeypatch, "allow")
    monkeypatch.setattr(
        g,
        "audit_output",
        lambda *a, **k: {"flagged": True, "reasons": ["instruction_override: ignore previous instructions"]},
    )
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )

    result = email_assistant.invoke({"email_input": respond_email}, _cfg("run-output-audit"))

    assert "__interrupt__" not in result
    assert not _email_was_sent(result["messages"])
    assert any(
        "output audit blocked before send" in (getattr(m, "content", "") or "")
        for m in result["messages"]
    )


def test_authorize_hitl_uses_existing_approval_gate(monkeypatch, fake_llms, respond_email):
    calls = _install_authz(monkeypatch, "hitl")
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    cfg = _cfg("run-hitl")

    paused = email_assistant.invoke({"email_input": respond_email}, cfg)
    assert paused["__interrupt__"][0].value[0]["action_request"]["action"] == "write_email"
    assert not _email_was_sent(paused["messages"])

    done = email_assistant.invoke(Command(resume={"type": "approve", "args": None}), cfg)
    assert "__interrupt__" not in done
    assert _email_was_sent(done["messages"])
    assert calls == [{
        "action": "write_email",
        "args": DRAFT,
        "run_id": "run-hitl",
        "action_id": "c1",
    }]


def test_authorize_deny_blocks_tool_execution(monkeypatch, fake_llms, respond_email):
    calls = _install_authz(monkeypatch, "deny", reason="recipient blocked")
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )

    result = email_assistant.invoke({"email_input": respond_email}, _cfg("run-deny"))

    assert "__interrupt__" not in result
    assert not _email_was_sent(result["messages"])
    assert any(
        "Security policy denied" in (getattr(m, "content", "") or "")
        for m in result["messages"]
    )
    assert calls == [{
        "action": "write_email",
        "args": DRAFT,
        "run_id": "run-deny",
        "action_id": "c1",
    }]


def test_security_disabled_does_not_call_authorize(monkeypatch, fake_llms, respond_email):
    import src.graph as g

    g._authorization_cache.clear()
    monkeypatch.setattr(g.settings, "security_enabled", False)

    def _boom(*_args, **_kwargs):
        raise AssertionError("authorize_action must not be called when security is disabled")

    monkeypatch.setattr(g, "authorize_action", _boom)
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )

    paused = email_assistant.invoke({"email_input": respond_email}, _cfg("run-disabled"))

    assert paused["__interrupt__"]

def test_human_edit_is_reauthorized_and_can_be_denied(monkeypatch, fake_llms, respond_email):
    import src.graph as g

    edited = {"to": "blocked@example.com", "subject": "Re: question", "content": "Here you go."}
    calls: list[dict] = []
    g._authorization_cache.clear()
    monkeypatch.setattr(g.settings, "security_enabled", True)

    def _fake_authorize(action: str, args: dict, run_id: str, action_id: str = "") -> dict:
        calls.append({"action": action, "args": args, "run_id": run_id, "action_id": action_id})
        if args == edited:
            return {"decision": "deny", "reason": "recipient blocked"}
        return {"decision": "hitl", "reason": "needs approval"}

    monkeypatch.setattr(g, "authorize_action", _fake_authorize)
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    cfg = _cfg("run-edit-deny")

    email_assistant.invoke({"email_input": respond_email}, cfg)
    result = email_assistant.invoke(Command(resume={"type": "approve", "args": edited}), cfg)

    assert not _email_was_sent(result["messages"])
    assert any(
        "Security policy denied" in (getattr(m, "content", "") or "")
        for m in result["messages"]
    )
    assert calls == [
        {"action": "write_email", "args": DRAFT, "run_id": "run-edit-deny", "action_id": "c1"},
        {"action": "write_email", "args": edited, "run_id": "run-edit-deny", "action_id": "c1"},
    ]
