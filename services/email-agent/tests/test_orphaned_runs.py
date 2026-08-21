"""A run row can outlive its checkpoint (storage backend switched, checkpoint DB
pruned, restore from an older dump). Before this the approval queue kept showing
those runs as live cards whose every button answered `Unknown run_id`."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from src import api
from src import run_registry
from src.api import app, reconcile_orphaned_runs
from src.run_registry import get_run, upsert_run
from src.tenant import current_agent_instance_id


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """The default index is the service's own logs/run_index.json — a test that
    reconciles "every pending run" must not be looking at the developer's."""
    monkeypatch.setattr(run_registry, "DEFAULT_RUN_INDEX", tmp_path / "run_index.json")
    monkeypatch.setattr(api, "_reconciled_instances", set())


class _EmptyState:
    values: dict = {}


class _StatefulState:
    values = {"email_input": {"subject": "still here"}}


class _FakeGraph:
    """Answers with state only for the thread ids it was told about."""

    def __init__(self, known_thread_ids=()):
        self.known = set(known_thread_ids)

    async def aget_state(self, config):
        thread_id = config["configurable"]["thread_id"]
        return _StatefulState() if thread_id in self.known else _EmptyState()


def _pending(run_id: str, subject: str = "Demande de devis") -> dict:
    return upsert_run(
        run_id,
        "pending_approval",
        email_input={"subject": subject, "author": "client@example.com"},
        pending_action=[{"action_request": {"action": "write_email", "args": {}}}],
        agent_instance_id=current_agent_instance_id(),
    )


def test_reconcile_marks_runs_without_checkpoint_orphaned():
    _pending("run-gone")
    _pending("run-alive")

    retired = asyncio.run(reconcile_orphaned_runs(_FakeGraph(known_thread_ids=["run-alive"])))

    assert retired == 1
    assert get_run("run-gone", agent_instance_id=current_agent_instance_id())["status"] == "orphaned"
    assert get_run("run-alive", agent_instance_id=current_agent_instance_id())["status"] == "pending_approval"


def test_orphaned_run_is_dropped_from_the_pending_listing():
    _pending("run-listing-gone")
    _pending("run-listing-alive")

    with TestClient(app) as client:
        client.app.state.graph = _FakeGraph(known_thread_ids=["run-listing-alive"])
        response = client.get("/runs?status=pending_approval")

    assert response.status_code == 200
    listed = {run["run_id"] for run in response.json()["runs"]}
    assert "run-listing-gone" not in listed
    assert "run-listing-alive" in listed


def test_decision_on_an_orphaned_run_answers_410_not_404():
    """404 read as "that run never existed"; the run does exist, its state does not."""
    _pending("run-decision-gone")

    with TestClient(app) as client:
        client.app.state.graph = _FakeGraph()
        response = client.post("/run/run-decision-gone/approve", json={})

    assert response.status_code == 410
    assert "expired" in response.json()["detail"]
    assert get_run("run-decision-gone", agent_instance_id=current_agent_instance_id())["status"] == "orphaned"


def test_unknown_run_id_still_answers_404():
    with TestClient(app) as client:
        client.app.state.graph = _FakeGraph()
        response = client.post("/run/never-existed/approve", json={})

    assert response.status_code == 404
    assert "Unknown run_id" in response.json()["detail"]


def test_a_run_that_never_had_graph_state_is_still_readable():
    """A message stopped by the junk gate is filed straight into the registry and
    never reaches the graph. Requiring a checkpoint to read it answered "this run
    has expired" for runs that had simply never needed one — and that is exactly
    the run someone lands on after 'Forcer l'agent' on bulk mail."""
    upsert_run(
        "run-junk-gated",
        "completed",
        email_input={
            "subject": "Soldes de printemps",
            "author": "news@shop.example",
            "junk_reason": "gmail:category_promotions",
        },
        classification="ignore",
        pending_action=None,
        agent_instance_id=current_agent_instance_id(),
    )

    with TestClient(app) as client:
        client.app.state.graph = _FakeGraph()  # no state for any thread
        response = client.get("/run/run-junk-gated/detail")

    assert response.status_code == 200
    body = response.json()
    assert body["has_graph_state"] is False
    assert body["subject"] == "Soldes de printemps"
    assert body["junk_reason"] == "gmail:category_promotions"
    assert body["classification"] == "ignore"
