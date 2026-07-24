from __future__ import annotations

import pytest
from conftest import ai_tool_call
from fastapi.testclient import TestClient

from src.api import app

# LangGraph's checkpoint/store tables are library-owned (created by
# checkpointer.setup(), not our Alembic migrations) and the async postgres
# saver connects directly with settings.database_url — it never binds the
# agora.user_id / agora.agent_instance_id session GUCs the 0009_tenant_rls
# policies check, so RLS cannot be layered onto them the way it is on
# agent_runs/dlq/etc. The actual isolation boundary is application-level:
# every run-scoped endpoint below resolves ownership via get_run_record
# (agent_instance_id-scoped) BEFORE touching graph state — see _require_run
# in src/api.py. This suite is the regression test for that guarantee: it
# proves cross-instance access is refused at every mutating and reading
# endpoint, end-to-end through the real API and graph.

DRAFT = {"to": "alice@example.com", "subject": "Re: question", "content": "Here you go."}
# The owning run is created under the DEFAULT instance (no header) on purpose:
# _invoke_graph in src/api.py reloads real LLM bindings via reload_config()
# whenever a request's instance differs from the process-global last-seen
# instance, which would clobber the fake_llms mock mid-test. Only the
# *attacking* cross-instance requests below carry a different instance header
# — they 404 inside _require_run's ownership check before ever reaching
# _invoke_graph, so they never trigger that reload.
OTHER_INSTANCE = "hr-email-agent"


@pytest.fixture(autouse=True)
def _isolate_graph_runtime_instance(monkeypatch):
    """Safety net: restore api._graph_runtime_instance_id after each test so a
    reload_config() triggered here can't leak a real (non-faked) LLM binding
    into an unrelated later test file."""
    import src.api as api_module

    monkeypatch.setattr(api_module, "_graph_runtime_instance_id", api_module._graph_runtime_instance_id)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _create_pending_run(client, fake_llms, respond_email) -> str:
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    run = client.post("/run", json=respond_email).json()
    assert run["status"] == "pending_approval"
    return run["run_id"]


def _other_instance_headers() -> dict:
    return {"X-Agora-Agent-Instance": OTHER_INSTANCE}


def test_get_run_hides_another_instances_run(client, fake_llms, respond_email):
    run_id = _create_pending_run(client, fake_llms, respond_email)

    resp = client.get(f"/run/{run_id}", headers=_other_instance_headers())

    assert resp.status_code == 404


def test_get_run_detail_hides_another_instances_run(client, fake_llms, respond_email):
    run_id = _create_pending_run(client, fake_llms, respond_email)

    resp = client.get(f"/run/{run_id}/detail", headers=_other_instance_headers())

    assert resp.status_code == 404


def test_approve_refuses_another_instances_run(client, fake_llms, respond_email):
    run_id = _create_pending_run(client, fake_llms, respond_email)

    resp = client.post(f"/run/{run_id}/approve", json={}, headers=_other_instance_headers())

    assert resp.status_code == 404


def test_reject_refuses_another_instances_run(client, fake_llms, respond_email):
    run_id = _create_pending_run(client, fake_llms, respond_email)

    resp = client.post(f"/run/{run_id}/reject", headers=_other_instance_headers())

    assert resp.status_code == 404


def test_respond_refuses_another_instances_run(client, fake_llms, respond_email):
    run_id = _create_pending_run(client, fake_llms, respond_email)

    resp = client.post(
        f"/run/{run_id}/respond",
        json={"feedback": "make it shorter"},
        headers=_other_instance_headers(),
    )

    assert resp.status_code == 404


def test_bulk_decision_reports_denied_for_another_instances_run(client, fake_llms, respond_email):
    run_id = _create_pending_run(client, fake_llms, respond_email)

    resp = client.post(
        "/runs/bulk",
        json={"run_ids": [run_id], "decision": "approve"},
        headers=_other_instance_headers(),
    )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["run_id"] == run_id
    assert results[0]["status"] in {"error", "denied"}


def test_owning_instance_can_still_approve_its_own_run(client, fake_llms, respond_email):
    """Control: the isolation checks above aren't just refusing everyone."""
    run_id = _create_pending_run(client, fake_llms, respond_email)

    resp = client.post(f"/run/{run_id}/approve", json={})

    assert resp.status_code == 200
    assert resp.json()["status"] != "pending_approval"
