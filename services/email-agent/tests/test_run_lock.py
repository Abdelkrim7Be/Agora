from __future__ import annotations

import os
import threading
import time

import pytest

from src.config import settings
from src.run_lock import try_claim_message

APP_URL = os.getenv("RLS_TEST_APP_URL", "")


def test_file_backend_rejects_concurrent_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "token_work_dir", str(tmp_path))

    with try_claim_message("agent-a", "msg-1") as first:
        assert first is True
        with try_claim_message("agent-a", "msg-1") as second:
            assert second is False


def test_file_backend_allows_reclaim_after_release(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "token_work_dir", str(tmp_path))

    with try_claim_message("agent-a", "msg-1") as first:
        assert first is True

    with try_claim_message("agent-a", "msg-1") as second:
        assert second is True


def test_file_backend_is_scoped_per_instance_and_message(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "token_work_dir", str(tmp_path))

    with try_claim_message("agent-a", "msg-1") as owner:
        assert owner is True
        with try_claim_message("agent-a", "msg-2") as other_message:
            assert other_message is True
        with try_claim_message("agent-b", "msg-1") as other_instance:
            assert other_instance is True


def test_file_backend_serializes_racing_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "token_work_dir", str(tmp_path))

    claims: list[bool] = []
    lock = threading.Lock()

    def attempt():
        with try_claim_message("agent-a", "msg-1") as acquired:
            with lock:
                claims.append(acquired)
            if acquired:
                time.sleep(0.05)

    threads = [threading.Thread(target=attempt) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert claims.count(True) == 1


@pytest.mark.skipif(not APP_URL, reason="RLS_TEST_APP_URL is required")
def test_postgres_backend_rejects_concurrent_claim(monkeypatch):
    monkeypatch.setattr(settings, "database_url", APP_URL)

    with try_claim_message("agent-a", "msg-pg-1") as first:
        assert first is True
        with try_claim_message("agent-a", "msg-pg-1") as second:
            assert second is False

    with try_claim_message("agent-a", "msg-pg-1") as reclaimed:
        assert reclaimed is True
