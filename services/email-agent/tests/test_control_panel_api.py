from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from src.api import app, _require_run, _run_detail
from src.run_registry import list_runs, selected_run_registry_backend, upsert_run
from src.tenant import current_user_id, normalize_user_id


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


def test_run_registry_filters_by_user(tmp_path):
    path = tmp_path / "runs.json"
    upsert_run("run-1", "completed", path=path, user_id="alice@example.com")
    upsert_run("run-2", "completed", path=path, user_id="bob@example.com")

    runs = list_runs(path=path, user_id="alice@example.com")

    assert [run["run_id"] for run in runs] == ["run-1"]
    assert runs[0]["user_id"] == "alice@example.com"


def test_selected_run_registry_backend_defaults_to_json(monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings, "run_registry_backend", "json")

    assert selected_run_registry_backend() == "json"


def test_selected_run_registry_backend_requires_database_url(monkeypatch):
    from src.config import settings
    import pytest

    monkeypatch.setattr(settings, "run_registry_backend", "postgres")
    monkeypatch.setattr(settings, "database_url", "")

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        selected_run_registry_backend()


def test_runs_endpoint_returns_registry(monkeypatch):
    captured = {}

    def fake_list_runs(status=None, user_id=None):
        captured["user_id"] = user_id
        return [{"run_id": "run-1", "status": status, "user_id": user_id}]

    monkeypatch.setattr("src.api.list_runs", fake_list_runs)

    with TestClient(app) as client:
        response = client.get("/runs?status=pending_approval", headers={"X-Agora-User": "alice@example.com"})

    assert response.status_code == 200
    assert captured["user_id"] == "alice@example.com"
    assert response.json() == {
        "runs": [{"run_id": "run-1", "status": "pending_approval", "user_id": "alice@example.com"}]
    }


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


class _FakeAsyncStore:
    """Mirrors AsyncSqliteStore's async API (the production store rejects sync calls)."""

    def __init__(self):
        self.values = {}

    async def aget(self, ns, key):
        value = self.values.get((ns, key))
        return type("Item", (), {"value": value}) if value is not None else None

    async def aput(self, ns, key, value):
        self.values[(ns, key)] = value


def test_memory_put_then_get_roundtrips(monkeypatch):
    with TestClient(app) as client:
        client.app.state.store = _FakeAsyncStore()
        put = client.put(
            "/memory",
            json={"triage_preferences": "triage", "response_preferences": "response"},
        )
        assert put.status_code == 200
        assert put.json() == {
            "triage_preferences": "triage",
            "response_preferences": "response",
        }

        got = client.get("/memory")
        assert got.status_code == 200
        assert got.json() == {
            "triage_preferences": "triage",
            "response_preferences": "response",
        }


def test_memory_is_scoped_by_forwarded_user_header(monkeypatch):
    with TestClient(app) as client:
        client.app.state.store = _FakeAsyncStore()
        alice = {"X-Agora-User": "alice@example.com"}
        bob = {"X-Agora-User": "bob@example.com"}

        assert client.put(
            "/memory",
            headers=alice,
            json={"triage_preferences": "alice triage", "response_preferences": "alice response"},
        ).status_code == 200
        assert client.put(
            "/memory",
            headers=bob,
            json={"triage_preferences": "bob triage", "response_preferences": "bob response"},
        ).status_code == 200

        assert client.get("/memory", headers=alice).json() == {
            "triage_preferences": "alice triage",
            "response_preferences": "alice response",
        }
        assert client.get("/memory", headers=bob).json() == {
            "triage_preferences": "bob triage",
            "response_preferences": "bob response",
        }

        stored_namespaces = {ns for ns, _ in client.app.state.store.values}
        assert ("email_agent", normalize_user_id("alice@example.com"), "triage_preferences") in stored_namespaces
        assert ("email_agent", normalize_user_id("bob@example.com"), "triage_preferences") in stored_namespaces


def test_policy_endpoint_proxies_security_service(monkeypatch):
    async def fake_fetch_policy():
        return {"policy_yaml": "default: deny\n"}

    monkeypatch.setattr("src.api.fetch_policy", fake_fetch_policy)

    with TestClient(app) as client:
        response = client.get("/policy")

    assert response.status_code == 200
    assert response.json() == {"policy_yaml": "default: deny\n"}


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


async def test_require_run_rejects_other_users_run(monkeypatch):
    import pytest

    class Graph:
        async def aget_state(self, config):
            raise AssertionError("graph state should not be read for another user")

    monkeypatch.setattr("src.api.get_run_record", lambda run_id, user_id=None: None)

    with pytest.raises(Exception) as exc:
        await _require_run(Graph(), "run-1")

    assert getattr(exc.value, "status_code", None) == 404


async def test_require_run_allows_owned_run(monkeypatch):
    class State:
        values = {"email_input": {"subject": "hello"}}

    class Graph:
        async def aget_state(self, config):
            return State()

    monkeypatch.setattr("src.api.get_run_record", lambda run_id, user_id=None: {"run_id": run_id})

    assert await _require_run(Graph(), "run-1") == {"configurable": {"thread_id": "run-1"}}


def _pubsub_body(payload: dict) -> dict:
    data = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    return {"message": {"data": data, "messageId": "msg-1"}}


def test_gmail_webhook_ignored_when_disabled(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", False)

    with TestClient(app) as client:
        response = client.post("/webhooks/gmail", json=_pubsub_body({"historyId": "123"}))

    assert response.status_code == 202
    assert response.json() == {"accepted": False, "reason": "gmail webhooks disabled"}


def test_gmail_webhook_processes_history_under_gmail_user(monkeypatch):
    import src.api as api

    captured = {}

    async def fake_poll_history(graph, history_id):
        captured["history_id"] = history_id
        captured["user_id"] = current_user_id()
        return [("m1", "completed", "run-1")]

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(api.settings, "gmail_webhook_secret", "secret")
    monkeypatch.setattr(api, "poll_history", fake_poll_history)

    with TestClient(app) as client:
        client.app.state.graph = object()
        response = client.post(
            "/webhooks/gmail?token=secret",
            json=_pubsub_body({"emailAddress": "alice@example.com", "historyId": "123"}),
        )

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "history_id": "123",
        "outcomes": [["m1", "completed", "run-1"]],
    }
    assert captured == {"history_id": "123", "user_id": "alice@example.com"}


def test_gmail_webhook_rejects_invalid_token_when_enabled(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(api.settings, "gmail_webhook_secret", "secret")

    with TestClient(app) as client:
        response = client.post("/webhooks/gmail?token=wrong", json=_pubsub_body({"historyId": "123"}))

    assert response.status_code == 403
