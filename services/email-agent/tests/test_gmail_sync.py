from __future__ import annotations

from src.config import settings
from src import gmail_sync
from src.tenant import user_context


def _use_json(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "run_registry_backend", "json")
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "gmail_sync_path", str(tmp_path / "gmail_sync.json"))


def test_baseline_round_trips_per_user(monkeypatch, tmp_path) -> None:
    _use_json(monkeypatch, tmp_path)

    assert gmail_sync.get_last_history_id("alice@example.com") is None
    gmail_sync.set_last_history_id("100", "alice@example.com")
    gmail_sync.set_last_history_id("250", "bob@example.com")

    assert gmail_sync.get_last_history_id("alice@example.com") == "100"
    assert gmail_sync.get_last_history_id("bob@example.com") == "250"


def test_baseline_only_advances_forward(monkeypatch, tmp_path) -> None:
    _use_json(monkeypatch, tmp_path)

    gmail_sync.set_last_history_id("200", "alice@example.com")
    # An out-of-order / stale push must not rewind the stored baseline.
    gmail_sync.set_last_history_id("150", "alice@example.com")
    assert gmail_sync.get_last_history_id("alice@example.com") == "200"

    gmail_sync.set_last_history_id("300", "alice@example.com")
    assert gmail_sync.get_last_history_id("alice@example.com") == "300"


def test_baseline_uses_current_tenant_context(monkeypatch, tmp_path) -> None:
    _use_json(monkeypatch, tmp_path)

    with user_context("carol@example.com"):
        gmail_sync.set_last_history_id("42")
        assert gmail_sync.get_last_history_id() == "42"

    assert gmail_sync.get_last_history_id("carol@example.com") == "42"
