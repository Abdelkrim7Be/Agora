from __future__ import annotations

from datetime import datetime, timedelta, timezone

import src.poller as poller


def _iso(delta_hours: float) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=delta_hours)
    ).isoformat(timespec="seconds")


def test_watch_is_fresh_beyond_margin(monkeypatch):
    monkeypatch.setattr(
        poller, "get_status", lambda agent_instance_id=None: {"watch_expires_at": _iso(48)}
    )
    assert poller.watch_is_fresh("box-a") is True


def test_watch_is_stale_inside_margin(monkeypatch):
    monkeypatch.setattr(
        poller, "get_status", lambda agent_instance_id=None: {"watch_expires_at": _iso(2)}
    )
    assert poller.watch_is_fresh("box-a") is False


def test_watch_without_recorded_expiration_is_stale(monkeypatch):
    monkeypatch.setattr(
        poller, "get_status", lambda agent_instance_id=None: {"watch_expires_at": None}
    )
    assert poller.watch_is_fresh("box-a") is False


def test_watch_with_unparseable_expiration_is_stale(monkeypatch):
    monkeypatch.setattr(
        poller, "get_status", lambda agent_instance_id=None: {"watch_expires_at": "not-a-date"}
    )
    assert poller.watch_is_fresh("box-a") is False


def _setup_watch_env(monkeypatch, fresh: bool):
    calls: list[str] = []
    monkeypatch.setattr(poller.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(poller, "active_email_agent_instance_ids", lambda: ["box-a"])
    monkeypatch.setattr(poller, "has_stored_token", lambda instance_id: True)
    monkeypatch.setattr(poller, "watch_is_fresh", lambda instance_id, margin_seconds=None: fresh)

    def fake_ensure_watch(resource=None):
        calls.append("watch")
        return {"historyId": "1", "expiration": "9999999999999"}

    monkeypatch.setattr(poller, "ensure_watch", fake_ensure_watch)
    return calls


def test_ensure_watches_skips_fresh_watch(monkeypatch):
    calls = _setup_watch_env(monkeypatch, fresh=True)
    results = poller.ensure_watches()
    assert calls == []
    assert results == {"box-a": None}


def test_ensure_watches_renews_stale_watch(monkeypatch):
    calls = _setup_watch_env(monkeypatch, fresh=False)
    results = poller.ensure_watches()
    assert calls == ["watch"]
    assert results["box-a"] is not None


def test_ensure_watches_force_renews_even_when_fresh(monkeypatch):
    calls = _setup_watch_env(monkeypatch, fresh=True)
    results = poller.ensure_watches(force=True)
    assert calls == ["watch"]
    assert results["box-a"] is not None


def test_ensure_watch_failure_does_not_block_other_instances(monkeypatch):
    monkeypatch.setattr(poller.settings, "gmail_webhook_enabled", True)
    monkeypatch.setattr(poller, "active_email_agent_instance_ids", lambda: ["box-a", "box-b"])
    monkeypatch.setattr(poller, "has_stored_token", lambda instance_id: True)
    monkeypatch.setattr(poller, "watch_is_fresh", lambda instance_id, margin_seconds=None: False)
    monkeypatch.setattr(poller, "record_failure", lambda *a, **k: None)

    def flaky_ensure_watch(resource=None):
        if poller.current_agent_instance_id() == "box-a":
            raise RuntimeError("watch quota")
        return {"historyId": "2"}

    monkeypatch.setattr(poller, "ensure_watch", flaky_ensure_watch)
    results = poller.ensure_watches()
    assert results["box-a"] is None
    assert results["box-b"] is not None
