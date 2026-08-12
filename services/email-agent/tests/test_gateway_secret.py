from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import app
from src.config import settings


def test_configured_gateway_secret_is_required(monkeypatch):
    monkeypatch.setattr(settings, "gateway_shared_secret", "correct-secret")

    with TestClient(app) as client:
        missing = client.get("/health")
        wrong = client.get("/health", headers={"X-Agora-Gateway-Secret": "wrong-secret"})

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_configured_gateway_secret_allows_request(monkeypatch):
    monkeypatch.setattr(settings, "gateway_shared_secret", "correct-secret")

    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Agora-Gateway-Secret": "correct-secret"})

    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "degraded"}
