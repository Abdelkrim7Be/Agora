from __future__ import annotations

import pytest
from conftest import ai_tool_call
from fastapi.testclient import TestClient

from src.api import app

DRAFT = {"to": "alice@example.com", "subject": "Re: question", "content": "Here you go."}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_double_approve_second_call_gets_409(client, fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    run = client.post("/run", json=respond_email).json()
    run_id = run["run_id"]

    first = client.post(f"/run/{run_id}/approve", json={})
    assert first.status_code == 200
    assert first.json()["status"] == "completed"

    second = client.post(f"/run/{run_id}/approve", json={})
    assert second.status_code == 409


def test_approve_then_reject_gets_409(client, fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    run = client.post("/run", json=respond_email).json()
    run_id = run["run_id"]

    approved = client.post(f"/run/{run_id}/approve", json={})
    assert approved.status_code == 200

    rejected = client.post(f"/run/{run_id}/reject")
    assert rejected.status_code == 409


def test_double_reject_second_call_gets_409(client, fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("write_email", DRAFT, "c1")],
    )
    run = client.post("/run", json=respond_email).json()
    run_id = run["run_id"]

    first = client.post(f"/run/{run_id}/reject")
    assert first.status_code == 200

    second = client.post(f"/run/{run_id}/reject")
    assert second.status_code == 409


def test_respond_after_decision_gets_409(client, fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    run = client.post("/run", json=respond_email).json()
    run_id = run["run_id"]

    approved = client.post(f"/run/{run_id}/approve", json={})
    assert approved.status_code == 200

    resp = client.post(f"/run/{run_id}/respond", json={"feedback": "shorter please"})
    assert resp.status_code == 409


def test_bulk_decision_on_already_decided_run_reports_error_not_crash(client, fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    run = client.post("/run", json=respond_email).json()
    run_id = run["run_id"]

    approved = client.post(f"/run/{run_id}/approve", json={})
    assert approved.status_code == 200

    bulk = client.post("/runs/bulk", json={"run_ids": [run_id], "decision": "approve"})
    assert bulk.status_code == 200
    result = bulk.json()["results"][0]
    assert result["run_id"] == run_id
    assert result["status"] == "error"
