from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src import health
from src.api import app
from src.config import settings


@pytest.fixture(autouse=True)
def _local_backend(monkeypatch):
    """Default to the fully-local dev backend so tests are deterministic
    regardless of the developer's real .env."""
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "security_enabled", False)


async def test_aggregate_health_reports_disabled_components_when_unconfigured():
    result = await health.aggregate_health()

    assert result["agent"]["status"] == "up"
    assert result["security"]["status"] == "disabled"
    assert result["database"]["status"] == "disabled"
    assert result["redis"]["status"] == "disabled"
    assert isinstance(result["queue_depth"], int)
    assert result["poll_job_queue"] == {"enabled": False}
    assert result["notifications"]["enabled"] is False


async def test_aggregate_health_never_raises_when_a_probe_is_down(monkeypatch):
    monkeypatch.setattr(settings, "security_enabled", True)
    monkeypatch.setattr(settings, "security_url", "http://localhost:1")  # nothing listens here

    result = await health.aggregate_health()

    assert result["security"]["status"] == "down"
    # The endpoint contract: a down component never raises / never 500s the pane.


async def test_security_up_when_probe_returns_2xx(monkeypatch):
    monkeypatch.setattr(settings, "security_enabled", True)

    class _FakeResponse:
        status_code = 200

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            return _FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient())

    result = await health.aggregate_health()
    assert result["security"]["status"] == "up"


def test_poller_component_up_after_recent_success(monkeypatch):
    monkeypatch.setattr(health, "latest_success_at", lambda: None)
    monkeypatch.setattr(
        health,
        "get_sync_status",
        lambda: {
            "last_success_at": "2026-07-09T10:00:00Z",
            "last_failure_at": None,
            "paused": False,
            "last_error": None,
        },
    )
    component = health._poller_component()
    assert component["status"] == "up"
    assert component["last_poll_at"] == "2026-07-09T10:00:00Z"


def test_poller_component_prefers_freshest_instance_poll(monkeypatch):
    monkeypatch.setattr(health, "latest_success_at", lambda: "2026-07-09T12:00:00Z")
    monkeypatch.setattr(
        health,
        "get_sync_status",
        lambda: {
            "last_success_at": "2026-07-09T10:00:00Z",
            "last_failure_at": None,
            "paused": False,
            "last_error": None,
        },
    )
    component = health._poller_component()
    assert component["last_poll_at"] == "2026-07-09T12:00:00Z"


def test_poller_component_down_when_failure_after_success(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_sync_status",
        lambda: {
            "last_success_at": "2026-07-09T09:00:00Z",
            "last_failure_at": "2026-07-09T10:00:00Z",
            "paused": False,
            "last_error": "connection reset",
        },
    )
    component = health._poller_component()
    assert component["status"] == "down"
    assert component["last_error"] == "connection reset"


def test_poller_component_up_when_success_after_failure(monkeypatch):
    monkeypatch.setattr(health, "latest_success_at", lambda: None)
    monkeypatch.setattr(
        health,
        "get_sync_status",
        lambda: {
            "last_success_at": "2026-07-09T11:00:00Z",
            "last_failure_at": "2026-07-09T10:00:00Z",
            "paused": False,
            "last_error": None,
        },
    )
    component = health._poller_component()
    assert component["status"] == "up"
    assert component["last_poll_at"] == "2026-07-09T11:00:00Z"


def test_poller_component_reports_paused_distinctly(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_sync_status",
        lambda: {"last_success_at": None, "last_failure_at": None, "paused": True, "last_error": None},
    )
    component = health._poller_component()
    assert component["status"] == "paused"


def test_queue_depth_counts_active_statuses_only(monkeypatch):
    def _fake_list_runs(status=None, user_id=None, agent_instance_id=None):
        return [{"run_id": "r1"}] if status == "pending_approval" else []

    monkeypatch.setattr(health, "list_runs", _fake_list_runs)
    assert health._queue_depth() == 1


def test_poll_job_queue_component_disabled_by_default():
    assert health._poll_job_queue_component() == {"enabled": False}


def test_poll_job_queue_component_reports_depth_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "job_queue_enabled", True)
    monkeypatch.setattr("src.job_queue.count_jobs", lambda status=None: 3 if status == "pending" else 1)

    result = health._poll_job_queue_component()

    assert result == {"enabled": True, "pending": 3, "processing": 1}


def test_database_component_disabled_when_no_database_url():
    assert health._database_component() == {"status": "disabled", "detail": "local sqlite/json backend"}


def test_redis_component_down_on_unreachable_host(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:1")
    assert health._redis_component()["status"] == "down"


def test_health_endpoint_returns_200_with_aggregated_body_even_when_a_component_is_down(monkeypatch):
    monkeypatch.setattr(settings, "security_enabled", True)
    monkeypatch.setattr(settings, "security_url", "http://localhost:1")

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["security"]["status"] == "down"
    assert body["agent"]["status"] == "up"
    assert "queue_depth" in body
