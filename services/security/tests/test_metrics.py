from __future__ import annotations

from fastapi.testclient import TestClient

from src.api import app
from src.metrics import inc_counter


def test_metrics_endpoint_exposes_pii_free_counters():
    inc_counter("agora_security_authorize_total", decision="allow", action="write_email")
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert "agora_security_authorize_total" in body
    assert "alice@example.com" not in body
