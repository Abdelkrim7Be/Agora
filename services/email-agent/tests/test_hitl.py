from __future__ import annotations

import uuid

import pytest
from conftest import ai_tool_call
from fastapi.testclient import TestClient
from langgraph.types import Command

from src.api import app
from src.capabilities import approval_required
from src.graph import email_assistant


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

DRAFT = {"to": "alice@example.com", "subject": "Re: question", "content": "Here you go."}
DRAFT2 = {"to": "alice@example.com", "subject": "Re: question", "content": "Shorter answer."}


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


def _email_was_sent(messages) -> bool:
    return any("Email sent to" in (getattr(m, "content", "") or "") for m in messages)


# --- Offline structural checks (no graph run) ---


def test_approval_required_aggregates_per_capability():
    assert approval_required({"email": True}) == {"write_email", "forward_email", "notify_internal", "reply_all"}
    assert approval_required({"email": True, "calendar": True}) == {
        "write_email",
        "forward_email",
        "notify_internal",
        "reply_all",
        "schedule_meeting",
    }
    assert approval_required({"email": True, "calendar": False}) == {
        "write_email", "forward_email", "notify_internal", "reply_all",
    }


def test_unknown_run_returns_404(client):
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
    assert paused["__interrupt__"][0].value[0]["action_request"]["action"] == "write_email"
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


def test_api_run_then_approve(client, fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )

    run = client.post("/run", json=respond_email).json()
    assert run["status"] == "pending_approval"
    assert run["pending_action"][0]["action_request"]["action"] == "write_email"

    approved = client.post(f"/run/{run['run_id']}/approve", json={}).json()
    assert approved["status"] == "completed"


def test_api_approve_reports_send_failure(client, fake_llms, respond_email, monkeypatch):
    import src.graph as g

    class FailingSendTool:
        def invoke(self, _args):
            raise RuntimeError("gmail token expired")

    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("write_email", DRAFT, "c1")],
    )
    monkeypatch.setitem(g.tools_by_name_map, "write_email", FailingSendTool())

    run = client.post("/run", json=respond_email).json()
    assert run["status"] == "pending_approval"

    approved = client.post(f"/run/{run['run_id']}/approve", json={}).json()
    assert approved["status"] == "failed"
    assert "gmail token expired" in approved["error"]


def test_api_respond_returns_pending_approval_on_redraft(client, fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("write_email", DRAFT, "c2"),
            ai_tool_call("Done", {"done": True}, "c3"),
        ],
    )

    run = client.post("/run", json=respond_email).json()
    assert run["status"] == "pending_approval"

    # Feedback triggers a re-draft — run pauses again on the second draft.
    responded = client.post(
        f"/run/{run['run_id']}/respond", json={"feedback": "make it shorter"}
    ).json()
    assert responded["status"] == "pending_approval"


def test_api_respond_forces_pending_when_model_tries_done(
    client, fake_llms, respond_email, tmp_path, monkeypatch
):
    import src.run_registry as rr

    monkeypatch.setattr(rr, "DEFAULT_RUN_INDEX", tmp_path / "runs.json")
    monkeypatch.setattr(rr.settings, "run_registry_backend", "json")
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c_done"),
        ],
        redraft_sequence=[DRAFT2],
    )

    run = client.post("/run", json=respond_email).json()
    assert run["status"] == "pending_approval"

    responded = client.post(
        f"/run/{run['run_id']}/respond", json={"feedback": "make it shorter"}
    ).json()

    assert responded["status"] == "pending_approval"
    assert responded["pending_action"][0]["action_request"]["args"] == DRAFT2
    pending = client.get("/runs?status=pending_approval").json()["runs"]
    assert [item["run_id"] for item in pending] == [run["run_id"]]
    assert pending[0]["pending_action"][0]["action_request"]["args"] == DRAFT2


def test_reject_can_record_rule_suggestion(fake_llms, respond_email, monkeypatch):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    calls: list[dict] = []
    rules_marker = object()

    import src.graph as g

    monkeypatch.setattr(g, "load_automation_rules", lambda: rules_marker)
    monkeypatch.setattr(
        g,
        "suggest_rule_from_correction",
        lambda rules, email_input, correction_type, details=None: calls.append({
            "rules": rules,
            "email_input": email_input,
            "correction_type": correction_type,
            "details": details,
        }) or True,
    )
    cfg = _cfg()

    email_assistant.invoke({"email_input": respond_email}, cfg)
    email_assistant.invoke(Command(resume={"type": "reject"}), cfg)

    assert calls == [{
        "rules": rules_marker,
        "email_input": {
            **respond_email,
            "category": None,
            "category_display_name": None,
            "priority": "normal",
            "workflow_owner": None,
            "workflow_approver": None,
            "workflow_instructions": None,
        },
        "correction_type": "ignored_draft",
        "details": {"tool": "write_email"},
    }]


def test_bulk_approve_two_runs(client, fake_llms, respond_email):
    # Both runs are created (each draws write_email) before any approval; the
    # approvals then draw Done. _FakeToolLLM repeats the last entry.
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("write_email", DRAFT, "c2"),
            ai_tool_call("Done", {"done": True}, "c3"),
        ],
    )
    run_ids = []
    for _ in range(2):
        run = client.post("/run", json=respond_email).json()
        assert run["status"] == "pending_approval"
        run_ids.append(run["run_id"])

    body = client.post("/runs/bulk", json={"run_ids": run_ids, "decision": "approve"}).json()
    assert len(body["results"]) == 2
    assert all(r["status"] == "completed" for r in body["results"])


def test_bulk_reject_two_runs(client, fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("write_email", DRAFT, "c2"),
            ai_tool_call("Done", {"done": True}, "c3"),
        ],
    )
    run_ids = []
    for _ in range(2):
        run = client.post("/run", json=respond_email).json()
        run_ids.append(run["run_id"])

    body = client.post("/runs/bulk", json={"run_ids": run_ids, "decision": "reject"}).json()
    assert len(body["results"]) == 2
    assert all(r["status"] in ("completed", "rejected") for r in body["results"])


def test_bulk_rejects_invalid_decision(client):
    resp = client.post("/runs/bulk", json={"run_ids": ["x"], "decision": "maybe"})
    assert resp.status_code == 400


def test_bulk_dept_forbidden_run_is_skipped_others_process(client, fake_llms, respond_email, monkeypatch):
    import src.api as api

    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    ok = client.post("/run", json=respond_email).json()["run_id"]

    real_get = api.get_run_record

    def fake_get(run_id, user_id=None, agent_instance_id=None):
        if run_id == "forbidden":
            return {"run_id": "forbidden", "status": "pending_approval", "workflow_dept": "Finance"}
        return real_get(run_id, user_id=user_id, agent_instance_id=agent_instance_id)

    monkeypatch.setattr(api, "get_run_record", fake_get)

    body = client.post(
        "/runs/bulk",
        json={"run_ids": ["forbidden", ok], "decision": "approve"},
        headers={"X-Agora-User-Dept": "RH"},
    ).json()
    by_id = {r["run_id"]: r for r in body["results"]}
    assert by_id["forbidden"]["status"] == "denied"
    assert by_id[ok]["status"] == "completed"
