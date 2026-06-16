from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import app, _run_detail
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


def test_run_detail_shapes_timeline_and_security():
    detail = _run_detail(
        {
            "email_input": {
                "author": "Alice",
                "to": "Me",
                "subject": "Question",
                "security": {"classification": "benign"},
            },
            "classification_decision": "respond",
            "messages": [
                {"role": "user", "content": "hello"},
            ],
        },
        "run-1",
    )

    assert detail["run_id"] == "run-1"
    assert detail["classification"] == "respond"
    assert detail["email"]["subject"] == "Question"
    assert detail["security"] == {"classification": "benign"}
    assert detail["timeline"] == [{"role": "user", "content": "hello", "tool_calls": []}]


def test_memory_endpoint_contract(monkeypatch):
    class FakeStore:
        def __init__(self):
            self.values = {}

        def get(self, ns, key):
            value = self.values.get((ns, key))
            return type("Item", (), {"value": value}) if value is not None else None

        def put(self, ns, key, value):
            self.values[(ns, key)] = value

    with TestClient(app) as client:
        client.app.state.store = FakeStore()
        response = client.put(
            "/memory",
            json={
                "triage_preferences": "triage",
                "response_preferences": "response",
            },
        )
        assert response.status_code == 200
        assert response.json() == {
            "triage_preferences": "triage",
            "response_preferences": "response",
        }


def test_update_agent_config_validates_and_writes(tmp_path, monkeypatch):
    import src.api as api

    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(api, "DEFAULT_CONFIG_PATH", config_path)
    payload = {
        "agent": {
            "background": "background",
            "triage_instructions": "triage",
            "response_preferences": "response",
        },
        "capabilities": {"email": True, "inbox": False},
        "auto_organize": {"enabled": False, "ignored_label": "Auto/Ignored"},
    }

    with TestClient(app) as client:
        response = client.put("/config", json=payload)

    assert response.status_code == 200
    assert response.json()["agent"]["background"] == "background"
    assert "background" in config_path.read_text()


def test_update_capabilities_preserves_valid_config(tmp_path, monkeypatch):
    import src.api as api

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """agent:
  background: background
  triage_instructions: triage
  response_preferences: response
capabilities:
  email: true
  calendar: false
auto_organize:
  enabled: false
  ignored_label: Auto/Ignored
"""
    )
    monkeypatch.setattr(api, "DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr("src.config.DEFAULT_CONFIG_PATH", config_path)

    with TestClient(app) as client:
        response = client.put(
            "/capabilities",
            json={"capabilities": {"email": True, "calendar": False, "inbox": True, "drafts": True}},
        )

    assert response.status_code == 200
    assert response.json()["capabilities"]["inbox"] is True
    assert "inbox: true" in config_path.read_text()


def test_update_rules_validates_yaml(tmp_path, monkeypatch):
    import src.api as api

    rules_path = tmp_path / "rules.yaml"
    monkeypatch.setattr(api, "DEFAULT_RULES_PATH", rules_path)

    with TestClient(app) as client:
        response = client.put(
            "/rules",
            json={
                "rules_yaml": """enabled: true
rules:
  - name: test rule
    then:
      notify: true
digest:
  enabled: true
  hour: 8
"""
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["parsed"]["enabled"] is True
    assert body["parsed"]["rules"][0]["name"] == "test rule"
    assert "test rule" in rules_path.read_text()
