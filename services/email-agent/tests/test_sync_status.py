from __future__ import annotations

from src.config import settings
from src import sync_status
from src.tenant import agent_instance_context, user_context


def _use_json(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "run_registry_backend", "json")
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "gmail_sync_status_path", str(tmp_path / "sync_status.json"))
    # An auth failure now raises a notification; keep it out of the real store.
    monkeypatch.setattr(settings, "notification_store_path", str(tmp_path / "notifications.json"))


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


def test_record_failure_sets_product_safe_error(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    sync_status.record_failure("Connection refused", "alice@example.com", "default-email-agent")
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert s["connection_status"] == "error"
    assert s["last_failure_at"] is not None
    assert s["last_error"] == "Gmail sync failed. Check service logs for details."


def test_auth_error_sets_expired(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    sync_status.record_failure("invalid_grant: Token has been expired", "alice@example.com", "default-email-agent")
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert s["connection_status"] == "expired"
    assert s["last_error"] == "Gmail authorization expired or was revoked. Reconnect Gmail."


def test_scope_change_sets_expired_and_names_the_fix(monkeypatch, tmp_path):
    # A token granted under a scope string this build no longer requests fails
    # the refresh with invalid_scope. Nothing expired and nothing was revoked, so
    # it used to fall through to the generic "check the logs" message and the
    # poller looped on it forever.
    _use_json(monkeypatch, tmp_path)
    sync_status.record_failure(
        "('invalid_scope: Bad Request', {'error': 'invalid_scope'})",
        "alice@example.com",
        "default-email-agent",
    )
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert s["connection_status"] == "expired"
    assert "Reconnect Gmail" in s["last_error"]


def test_auth_failure_raises_one_deduped_notification(monkeypatch, tmp_path):
    from src import notification_store

    _use_json(monkeypatch, tmp_path)
    for _ in range(3):
        sync_status.record_failure("invalid_scope: Bad Request", "alice@example.com", "default-email-agent")

    notifications = notification_store.list_notifications(
        user_id="alice@example.com", agent_instance_id="default-email-agent"
    )
    reconnects = [n for n in notifications if n["notification_type"] == "mailbox_reconnect_required"]
    # Three failed poll cycles, one notification: the poller runs every few
    # minutes and would otherwise bury every other notification.
    assert len(reconnects) == 1
    assert reconnects[0]["action_url"] == "/instance/default-email-agent/gmail"


def test_transient_failure_raises_no_notification(monkeypatch, tmp_path):
    from src import notification_store

    _use_json(monkeypatch, tmp_path)
    sync_status.record_failure("Connection refused", "alice@example.com", "default-email-agent")

    notifications = notification_store.list_notifications(
        user_id="alice@example.com", agent_instance_id="default-email-agent"
    )
    # Only a human-fixable authorization failure is worth interrupting for.
    assert [n for n in notifications if n["notification_type"] == "mailbox_reconnect_required"] == []


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


def test_delegated_users_share_instance_status(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    sync_status.record_success("polling", "alice@example.com", "default-email-agent")

    a = sync_status.get_status("alice@example.com", "default-email-agent")
    b = sync_status.get_status("bob@example.com", "default-email-agent")

    assert a["connection_status"] == "connected"
    assert b["connection_status"] == "connected"


def test_uses_tenant_context(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)

    with user_context("carol@example.com"):
        with agent_instance_context("support-email-agent"):
            sync_status.record_success("manual")
            s = sync_status.get_status()
            assert s["connection_status"] == "connected"
            assert s["user_id"] == settings.default_user_id
            assert s["agent_instance_id"] == "support-email-agent"

    assert sync_status.get_status("carol@example.com", "ceo-email-agent")["connection_status"] == "disconnected"


def test_gmail_rate_limit_error_names_gmail(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    raw_error = (
        "<HttpError 429 when requesting https://gmail.googleapis.com/gmail/v1/users/me/profile?alt=json "
        "returned \"User-rate limit exceeded. Retry after 2026-07-16T09:30:25.911Z\". "
        "Details: \"[{'reason': 'rateLimitExceeded'}]\">"
    )
    sync_status.record_failure(raw_error, "alice@example.com", "default-email-agent")
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert s["last_error"].startswith("Gmail rate limit reached.")
    assert "AI provider" not in s["last_error"]


def test_provider_rate_limit_error_is_sanitized(monkeypatch, tmp_path):
    _use_json(monkeypatch, tmp_path)
    raw_error = "Error code: 429 - {'error': {'message': 'Rate limit reached for model `llama-3.3-70b-versatile` in organization `org_secret`'}}"
    sync_status.record_failure(raw_error, "alice@example.com", "default-email-agent")
    s = sync_status.get_status("alice@example.com", "default-email-agent")
    assert s["last_error"] == "AI provider rate limit reached. Wait a few minutes and try again."
    assert "llama" not in s["last_error"]
    assert "org_secret" not in s["last_error"]
