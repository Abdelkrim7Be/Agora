from __future__ import annotations

from src.config import settings
from src import sync_status
from src.tenant import agent_instance_context, user_context


def _use_json(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "run_registry_backend", "json")
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "gmail_sync_status_path", str(tmp_path / "sync_status.json"))


def test_default_status_is_disconnected(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert s["connection_status"] == "disconnected"
    assert s["paused"] is False
    assert s["last_success_at"] is None
    assert s["last_error"] is None


def test_record_success_sets_connected(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    sync_status.record_success("polling", "alice@example.com", "default-email-agent")
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert s["connection_status"] == "connected"
    assert s["sync_mode"] == "polling"
    assert s["last_success_at"] is not None
    assert s["last_error"] is None


def test_record_failure_sets_error(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    sync_status.record_failure("Connection refused", "alice@example.com", "default-email-agent")
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert s["connection_status"] == "error"
    assert s["last_failure_at"] is not None
    assert "Connection refused" in s["last_error"]


def test_auth_error_sets_expired(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    sync_status.record_failure("invalid_grant: Token has been expired", "alice@example.com", "default-email-agent")
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert s["connection_status"] == "expired"


def test_record_success_clears_error(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    sync_status.record_failure("oops", "alice@example.com", "default-email-agent")
    sync_status.record_success("manual", "alice@example.com", "default-email-agent")
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert s["connection_status"] == "connected"
    assert s["last_error"] is None


def test_record_success_stores_watch_expires_at(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    sync_status.record_success(
        "webhook",
        "alice@example.com",
        "default-email-agent",
        watch_expires_at="2026-07-01T00:00:00+00:00",
    )
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert s["watch_expires_at"] == "2026-07-01T00:00:00+00:00"


def test_set_paused_round_trips(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    sync_status.set_paused(True, "alice@example.com", "default-email-agent")
    assert sync_status.get_status("alice@example.com", "default-email-agent")["paused"] is True
    sync_status.set_paused(False, "alice@example.com", "default-email-agent")
    assert sync_status.get_status("alice@example.com", "default-email-agent")["paused"] is False


def test_per_instance_isolation(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    sync_status.record_success("polling", "alice@example.com", "ceo-email-agent")
    sync_status.record_failure("oops", "alice@example.com", "hr-email-agent")

    ceo = sync_status.get_status("alice@example.com", "ceo-email-agent")
    hr = sync_status.get_status("alice@example.com", "hr-email-agent")

    assert ceo["connection_status"] == "connected"
    assert hr["connection_status"] == "error"


def test_per_user_isolation(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    sync_status.record_success("polling", "alice@example.com", "default-email-agent")
    sync_status.record_failure("oops", "bob@example.com", "default-email-agent")

    a = sync_status.get_status("alice@example.com", "default-email-agent")
    b = sync_status.get_status("bob@example.com", "default-email-agent")

    assert a["connection_status"] == "connected"
    assert b["connection_status"] == "error"


def test_uses_tenant_context(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)

    with user_context("carol@example.com"):
        with agent_instance_context("support-email-agent"):
            sync_status.record_success("manual")
            s = sync_status.get_status()
            assert s["connection_status"] == "connected"
            assert s["user_id"] == "carol@example.com"
            assert s["agent_instance_id"] == "support-email-agent"

    assert sync_status.get_status("carol@example.com", "ceo-email-agent")["connection_status"] == "disconnected"


def test_error_truncated_to_500_chars(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    long_error = "x" * 1000
    sync_status.record_failure(long_error, "alice@example.com", "default-email-agent")
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert len(s["last_error"]) == 500
