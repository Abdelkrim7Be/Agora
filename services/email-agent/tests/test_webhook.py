from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from src.api import app


def _pubsub_body(payload: dict) -> dict:
    data = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    return {"message": {"data": data, "messageId": "msg-1"}}


def test_duplicate_history_push_is_a_no_op(monkeypatch):
    import src.api as api

    called = {"poll": False, "set": False}

    async def fake_poll_history(graph, history_id):
        called["poll"] = True
        return []

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(api.settings, "gmail_webhook_secret", "secret")
    monkeypatch.setattr(api, "poll_history", fake_poll_history)
    monkeypatch.setattr(api, "get_last_history_id", lambda: "123")
    monkeypatch.setattr(api, "set_last_history_id", lambda hid: called.update(set=True))

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
    assert called == {"poll": False, "set": False}


def test_webhook_does_not_advance_baseline_on_generic_batch_failure(monkeypatch):
    import src.api as api

    advanced = {"called": False}
    failures: list[str] = []

    async def fake_poll_history(graph, history_id):
        raise RuntimeError("temporary upstream failure")

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(api.settings, "gmail_webhook_secret", "secret")
    monkeypatch.setattr(api, "poll_history", fake_poll_history)
    monkeypatch.setattr(api, "get_last_history_id", lambda: "100")
    monkeypatch.setattr(api, "set_last_history_id", lambda hid: advanced.update(called=True, hid=hid))
    monkeypatch.setattr(api, "record_sync_failure", lambda error: failures.append(error))

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
    assert advanced == {"called": False}
    assert failures == ["temporary upstream failure"]


def test_webhook_advances_stale_history_window_to_stop_redelivery(monkeypatch):
    import src.api as api

    advanced = {}
    failures: list[str] = []

    class _HttpError(Exception):
        def __init__(self, status):
            super().__init__("startHistoryId too old")
            self.resp = type("Resp", (), {"status": status})()

    async def fake_poll_history(graph, history_id):
        raise _HttpError(404)

    monkeypatch.setattr(api.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(api.settings, "gmail_webhook_secret", "secret")
    monkeypatch.setattr(api, "poll_history", fake_poll_history)
    monkeypatch.setattr(api, "get_last_history_id", lambda: "100")
    monkeypatch.setattr(api, "set_last_history_id", lambda hid: advanced.update(hid=hid))
    monkeypatch.setattr(api, "record_sync_failure", lambda error: failures.append(error))

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
    assert advanced == {"hid": "123"}
    assert failures == ["startHistoryId too old"]
