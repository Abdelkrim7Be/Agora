from __future__ import annotations

from fastapi.testclient import TestClient

import src.api as api
from src.api import app
from src.config import settings


def test_configured_gateway_secret_is_required(monkeypatch):
    monkeypatch.setattr(settings, "gateway_shared_secret", "correct-secret")

    with TestClient(app) as client:
        missing = client.get("/dlq", headers={"X-Agora-Instance-Role": "owner"})
        wrong = client.get(
            "/dlq",
            headers={
                "X-Agora-Gateway-Secret": "wrong-secret",
                "X-Agora-Instance-Role": "owner",
            },
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_configured_gateway_secret_allows_request(monkeypatch):
    monkeypatch.setattr(settings, "gateway_shared_secret", "correct-secret")
    monkeypatch.setattr(api, "list_dead_letters", lambda status=None, limit=100, agent_instance_id=None: [])

    with TestClient(app) as client:
        response = client.get(
            "/dlq",
            headers={
                "X-Agora-Gateway-Secret": "correct-secret",
                "X-Agora-Instance-Role": "owner",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"entries": [], "limit": 100}


def test_health_and_metrics_bypass_gateway_secret(monkeypatch):
    monkeypatch.setattr(settings, "gateway_shared_secret", "correct-secret")
    monkeypatch.setattr(api, "render_metrics", lambda: "agora_test 1\n")

    with TestClient(app) as client:
        health = client.get("/health")
        metrics = client.get("/metrics")

    assert health.status_code == 200
    assert health.json()["status"] in {"ok", "degraded"}
    assert metrics.status_code == 200
    assert "agora_test 1" in metrics.text
