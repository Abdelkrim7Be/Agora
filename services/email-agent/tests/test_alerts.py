from __future__ import annotations

from datetime import datetime, timezone

from src import alerts
from src.config import settings


def test_alerts_fire_once_on_state_change(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "notify_enabled", True)
    monkeypatch.setattr(settings, "alerts_path", str(tmp_path / "alerts.yaml"))
    monkeypatch.setattr(settings, "alerts_state_path", str(tmp_path / "alert_state.yaml"))
    alerts.save_alert_settings(alerts.AlertSettings(enabled=True, admin_recipient="ops@example.com"))

    sent = []
    monkeypatch.setattr(alerts, "notify_admin_alert", lambda recipient_hint, subject, body: sent.append((recipient_hint, subject, body)) or True)
    snapshot_up = {"poller": {"status": "up"}, "agent": {"status": "up"}, "security": {"status": "up"}, "database": {"status": "up"}, "redis": {"status": "up"}}
    snapshot_down = {"poller": {"status": "down", "last_error": "connection reset"}, "agent": {"status": "up"}, "security": {"status": "up"}, "database": {"status": "up"}, "redis": {"status": "up"}}

    assert alerts.evaluate_alerts(snapshot_up, now=datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)) == []
    first = alerts.evaluate_alerts(snapshot_down, now=datetime(2026, 7, 11, 10, 5, tzinfo=timezone.utc))
    assert len(first) == 1
    assert first[0]["kind"] == "component_down"
    assert len(sent) == 1

    assert alerts.evaluate_alerts(snapshot_down, now=datetime(2026, 7, 11, 10, 10, tzinfo=timezone.utc)) == []
    cleared = alerts.evaluate_alerts(snapshot_up, now=datetime(2026, 7, 11, 10, 15, tzinfo=timezone.utc))
    assert len(cleared) == 1
    assert cleared[0]["kind"] == "component_up"
    assert len(sent) == 2


def test_alerts_can_fire_on_first_observed_down_state(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "notify_enabled", True)
    monkeypatch.setattr(settings, "alerts_path", str(tmp_path / "alerts.yaml"))
    monkeypatch.setattr(settings, "alerts_state_path", str(tmp_path / "alert_state.yaml"))
    alerts.save_alert_settings(alerts.AlertSettings(enabled=True, admin_recipient="ops@example.com"))

    sent = []
    monkeypatch.setattr(alerts, "notify_admin_alert", lambda recipient_hint, subject, body: sent.append(subject) or True)
    snapshot_down = {"poller": {"status": "down", "last_error": "timeout"}, "agent": {"status": "up"}, "security": {"status": "up"}, "database": {"status": "up"}, "redis": {"status": "up"}}

    events = alerts.evaluate_alerts(snapshot_down, now=datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc))
    assert events[0]["kind"] == "component_down"
    assert len(sent) == 1


def test_alerts_disabled_do_not_send(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "notify_enabled", True)
    monkeypatch.setattr(settings, "alerts_path", str(tmp_path / "alerts.yaml"))
    monkeypatch.setattr(settings, "alerts_state_path", str(tmp_path / "alert_state.yaml"))
    alerts.save_alert_settings(alerts.AlertSettings(enabled=False, admin_recipient="ops@example.com"))

    sent = []
    monkeypatch.setattr(alerts, "notify_admin_alert", lambda recipient_hint, subject, body: sent.append(subject) or True)
    snapshot_down = {"poller": {"status": "down", "last_error": "timeout"}, "agent": {"status": "up"}, "security": {"status": "up"}, "database": {"status": "up"}, "redis": {"status": "up"}}

    events = alerts.evaluate_alerts(snapshot_down, now=datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc))
    assert events[0]["sent"] is False
    assert sent == []


def test_token_cap_alert_uses_threshold_transition(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "notify_enabled", True)
    monkeypatch.setattr(settings, "alerts_path", str(tmp_path / "alerts.yaml"))
    monkeypatch.setattr(settings, "alerts_state_path", str(tmp_path / "alert_state.yaml"))
    alerts.save_alert_settings(
        alerts.AlertSettings(
            enabled=True,
            admin_recipient="ops@example.com",
            token_cap_enabled=True,
            daily_token_cap=100,
            token_cap_threshold=0.9,
        )
    )

    sent = []
    monkeypatch.setattr(alerts, "notify_admin_alert", lambda recipient_hint, subject, body: sent.append(subject) or True)
    totals = iter([80, 95, 97])
    monkeypatch.setattr(
        alerts,
        "summarize_costs",
        lambda period, user_id=None, agent_instance_id=None: {"totals": {"total_tokens": next(totals)}}
    )
    snapshot = {"poller": {"status": "up"}, "agent": {"status": "up"}, "security": {"status": "up"}, "database": {"status": "up"}, "redis": {"status": "up"}}

    assert alerts.evaluate_alerts(snapshot, now=datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)) == []
    events = alerts.evaluate_alerts(snapshot, now=datetime(2026, 7, 11, 9, 5, tzinfo=timezone.utc))
    assert events[0]["kind"] == "token_cap_near"
    assert len(sent) == 1
    assert alerts.evaluate_alerts(snapshot, now=datetime(2026, 7, 11, 9, 10, tzinfo=timezone.utc)) == []
