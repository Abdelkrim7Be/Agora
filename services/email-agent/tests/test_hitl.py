from __future__ import annotations

import uuid

from conftest import ai_tool_call
from fastapi.testclient import TestClient
from langgraph.types import Command

from src.api import app
from src.capabilities import approval_required
from src.graph import email_assistant

client = TestClient(app)

DRAFT = {"to": "alice@example.com", "subject": "Re: question", "content": "Here you go."}


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


def _email_was_sent(messages) -> bool:
    return any("Email sent to" in (getattr(m, "content", "") or "") for m in messages)


# --- Offline structural checks (no graph run) ---


def test_approval_required_aggregates_per_capability():
    assert approval_required({"email": True}) == {"write_email"}
    assert approval_required({"email": True, "calendar": True}) == {
        "write_email",
        "schedule_meeting",
    }
    assert approval_required({"email": True, "calendar": False}) == {"write_email"}


def test_unknown_run_returns_404():
    resp = client.post("/run/does-not-exist/reject")
    assert resp.status_code == 404


# --- HITL cycle against the real graph (LLMs faked, no Groq) ---


def test_gated_action_pauses_then_approve_executes(fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    cfg = _cfg()

    paused = email_assistant.invoke({"email_input": respond_email}, cfg)
    assert paused["__interrupt__"][0].value["action"] == "write_email"
    assert not _email_was_sent(paused["messages"])

    done = email_assistant.invoke(Command(resume={"type": "approve", "args": None}), cfg)
    assert "__interrupt__" not in done
    assert _email_was_sent(done["messages"])


def test_gated_action_reject_does_not_execute(fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    cfg = _cfg()

    email_assistant.invoke({"email_input": respond_email}, cfg)
    result = email_assistant.invoke(Command(resume={"type": "reject"}), cfg)

    assert not _email_was_sent(result["messages"])


def test_api_run_then_approve(fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )

    run = client.post("/run", json=respond_email).json()
    assert run["status"] == "pending_approval"
    assert run["pending_action"]["action"] == "write_email"

    approved = client.post(f"/run/{run['run_id']}/approve", json={}).json()
    assert approved["status"] == "completed"

    state = email_assistant.get_state({"configurable": {"thread_id": run["run_id"]}})
    assert _email_was_sent(state.values["messages"])
