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


def test_run_registry_clears_pending_action_on_terminal_status(tmp_path):
    path = tmp_path / "runs.json"
    upsert_run(
        "run-1",
        "pending_approval",
        pending_action=[{"action_request": {"action": "write_email", "args": {}}}],
        path=path,
    )

    saved = upsert_run("run-1", "completed", path=path)

    assert saved["status"] == "completed"
    assert saved["pending_action"] is None
    assert list_runs(status="pending_approval", path=path) == []


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

    def fake_list_runs(status=None, user_id=None, limit=None, offset=0):
        captured["user_id"] = user_id
        captured["limit"] = limit
        captured["offset"] = offset
        return [{"run_id": "run-1", "status": status, "user_id": user_id}]

    monkeypatch.setattr("src.api.list_runs", fake_list_runs)

    with TestClient(app) as client:
        response = client.get("/runs?status=pending_approval", headers={"X-Agora-User": "alice@example.com"})

    assert response.status_code == 200
    assert captured["user_id"] == "alice@example.com"
    assert response.json() == {
        "runs": [{"run_id": "run-1", "status": "pending_approval", "user_id": "alice@example.com"}],
        "limit": 50,
        "offset": 0,
        "has_more": False,
    }


def test_sync_endpoint_polls_unread_for_current_user(monkeypatch):
    import src.api as api

    captured = {}
    graph = object()

    def fake_gmail_resource(user_id=None):
        captured["gmail_user_id"] = user_id
        return "gmail"

    async def fake_poll_once(graph_arg, resource=None, max_results=None):
        captured["graph"] = graph_arg
        captured["resource"] = resource
        captured["max_results"] = max_results
        captured["current_user"] = current_user_id()
        return [("msg-1", "pending_approval", "run-1")]

    monkeypatch.setattr(api, "gmail_resource", fake_gmail_resource)
    monkeypatch.setattr(api, "poll_once", fake_poll_once)

    with TestClient(app) as client:
        client.app.state.graph = graph
        response = client.post("/sync?limit=7", headers={"X-Agora-User": "owner"})

    assert response.status_code == 200
    assert response.json() == {"outcomes": [["msg-1", "pending_approval", "run-1"]]}
    assert captured == {
        "gmail_user_id": "owner",
        "graph": graph,
        "resource": "gmail",
        "max_results": 7,
        "current_user": "owner",
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


def test_respond_keeps_pending_when_redraft_fails(monkeypatch):
    pending_action = [{"action_request": {"action": "write_email", "args": {"content": "draft"}}}]

    class State:
        values = {"email_input": {"subject": "hello"}}

    class Graph:
        async def aget_state(self, config):
            return State()

        async def ainvoke(self, command, config):
            raise RuntimeError("network unreachable")

    def fake_get_run(run_id, user_id=None):
        return {
            "run_id": run_id,
            "status": "pending_approval",
            "classification": "respond",
            "pending_action": pending_action,
        }

    monkeypatch.setattr("src.api.get_run_record", fake_get_run)

    with TestClient(app) as client:
        client.app.state.graph = Graph()
        response = client.post("/run/run-1/respond", json={"feedback": "shorter"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["pending_action"] == pending_action
    assert "draft is still pending" in body["error"]


def test_decision_completes_from_pending_action_when_graph_fails(monkeypatch):
    import src.api as api

    draft = {"to": "alice@example.com", "subject": "Re", "content": "draft"}
    pending_action = [{"action_request": {"action": "write_email", "args": draft}}]
    tool_calls = []
    saved = []

    class Tool:
        def invoke(self, args):
            tool_calls.append(args)
            return "sent"

    class State:
        values = {"email_input": {"subject": "hello"}}

    class Graph:
        async def aget_state(self, config):
            return State()

        async def ainvoke(self, command, config):
            raise RuntimeError("rate limited")

    monkeypatch.setitem(api.graph_module.tools_by_name_map, "write_email", Tool())
    monkeypatch.setattr(api, "_record_response", lambda response, email_input=None: saved.append((response, email_input)))
    monkeypatch.setattr(api, "get_run_record", lambda run_id, user_id=None: {
        "run_id": run_id,
        "status": "pending_approval",
        "classification": "respond",
        "pending_action": pending_action,
        "subject": "hello",
        "author": "Alice",
        "email_id": "msg-1",
        "gmail_thread_id": "thread-1",
    })

    with TestClient(app) as client:
        client.app.state.graph = Graph()
        approve = client.post("/run/run-1/approve", json={})
        reject = client.post("/run/run-1/reject")

    assert approve.status_code == 200
    assert approve.json()["status"] == "completed"
    assert tool_calls == [draft]
    assert reject.status_code == 200
    assert reject.json()["status"] == "completed"
    assert [item[0].status for item in saved] == ["completed", "completed"]


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


def test_gmail_webhook_processes_history_from_stored_baseline(monkeypatch):
    import src.api as api

    captured = {}
    advanced = {}

    async def fake_poll_history(graph, history_id):
        # Must query from the *previous* baseline, not the just-pushed id, or the
        # message that fired the push is excluded by Gmail's history semantics.
        captured["history_id"] = history_id
        captured["user_id"] = current_user_id()
        return [("m1", "completed", "run-1")]

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(api.settings, "gmail_webhook_secret", "secret")
    monkeypatch.setattr(api, "poll_history", fake_poll_history)
    monkeypatch.setattr(api, "get_last_history_id", lambda: "100")
    monkeypatch.setattr(api, "set_last_history_id", lambda hid: advanced.update(hid=hid))

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
    assert captured == {"history_id": "100", "user_id": "alice@example.com"}
    # Baseline advanced to the pushed id once processing succeeds.
    assert advanced == {"hid": "123"}


def test_gmail_webhook_seeds_baseline_on_first_push(monkeypatch):
    import src.api as api

    advanced = {}
    called = {"poll": False}

    async def fake_poll_history(graph, history_id):
        called["poll"] = True
        return []

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(api.settings, "gmail_webhook_secret", "secret")
    monkeypatch.setattr(api, "poll_history", fake_poll_history)
    monkeypatch.setattr(api, "get_last_history_id", lambda: None)
    monkeypatch.setattr(api, "set_last_history_id", lambda hid: advanced.update(hid=hid))

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
        "outcomes": [],
        "synced": False,
    }
    # No baseline yet → seed it and wait for the next push instead of querying blind.
    assert advanced == {"hid": "123"}
    assert called["poll"] is False


def test_gmail_webhook_rejects_invalid_token_when_enabled(monkeypatch):
    import src.api as api

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(api.settings, "gmail_webhook_secret", "secret")

    with TestClient(app) as client:
        response = client.post("/webhooks/gmail?token=wrong", json=_pubsub_body({"historyId": "123"}))

    assert response.status_code == 403


def test_inbox_returns_agent_known_messages_when_gmail_unavailable(monkeypatch):
    import src.api as api

    def unavailable(_user_id=None):
        raise RuntimeError("network unavailable")

    def runs(**_kwargs):
        return [
            {
                "run_id": "run-1",
                "status": "pending_approval",
                "email_id": "msg-1",
                "gmail_thread_id": "thread-1",
                "author": "Alice <alice@example.com>",
                "subject": "Need approval",
                "updated_at": "2026-06-17T12:00:00Z",
                "classification": {"urgency": "high"},
            }
        ]

    monkeypatch.setattr(api, "gmail_resource", unavailable)
    monkeypatch.setattr(api, "list_runs", runs)

    with TestClient(app) as client:
        response = client.get("/inbox", headers={"X-Agora-User": "owner"})

    assert response.status_code == 200
    assert response.json() == {
        "messages": [
            {
                "id": "msg-1",
                "thread_id": "thread-1",
                "from": "Alice <alice@example.com>",
                "subject": "Need approval",
                "snippet": "Gmail is temporarily unavailable; showing the last agent-known message.",
                "date": "2026-06-17T12:00:00Z",
                "unread": True,
                "run_id": "run-1",
                "run_status": "pending_approval",
                "classification": {"urgency": "high"},
                "stale": True,
            }
        ],
        "warning": (
            "Gmail inbox is unavailable. Check OAuth credentials and container network access. "
            "Showing last known agent messages."
        ),
    }


def test_inbox_action_returns_503_when_gmail_unavailable(monkeypatch):
    import src.api as api

    def unavailable(_user_id=None):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(api, "gmail_resource", unavailable)

    with TestClient(app) as client:
        response = client.post("/inbox/msg-1/archive", headers={"X-Agora-User": "owner"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Gmail inbox is unavailable. Check OAuth credentials and container network access."
    }
