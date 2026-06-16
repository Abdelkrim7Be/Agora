from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import app
from src.run_registry import list_runs, upsert_run


def test_run_registry_filters_by_status(tmp_path):
    path = tmp_path / "runs.json"
    upsert_run(
        "run-1",
        "pending_approval",
        email_input={"subject": "Question", "author": "Alice"},
        pending_action=[{"action_request": {"action": "write_email", "args": {}}}],
        path=path,
    )
    upsert_run("run-2", "completed", path=path)

    pending = list_runs(status="pending_approval", path=path)

    assert [run["run_id"] for run in pending] == ["run-1"]
    assert pending[0]["subject"] == "Question"
    assert pending[0]["pending_action"][0]["action_request"]["action"] == "write_email"


def test_runs_endpoint_returns_registry(monkeypatch):
    monkeypatch.setattr(
        "src.api.list_runs",
        lambda status=None: [{"run_id": "run-1", "status": status}],
    )

    with TestClient(app) as client:
        response = client.get("/runs?status=pending_approval")

    assert response.status_code == 200
    assert response.json() == {"runs": [{"run_id": "run-1", "status": "pending_approval"}]}
