from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import src.api as api
import src.dlq as dlq
from src.api import app
from src.config import settings


def test_retry_exhausted_message_is_recorded_in_dlq(monkeypatch, tmp_path):
    path = tmp_path / "dlq.json"
    monkeypatch.setattr(settings, "dlq_backend", "json")
    monkeypatch.setattr(settings, "dlq_path", str(path))

    entry = dlq.record_dead_letter(
        {
            "message_id": "msg-1",
            "reason": "retry_exhausted",
            "error": "429 rate_limit_exceeded",
            "payload": {"email_id": "msg-1", "subject": "Hello"},
        },
        path=path,
    )

    rows = dlq.list_dead_letters(path=path, limit=10)
    assert rows[0]["entry_id"] == entry["entry_id"]
    assert rows[0]["reason"] == "retry_exhausted"
    assert rows[0]["payload"]["subject"] == "Hello"


def test_requeue_claim_is_idempotent(monkeypatch, tmp_path):
    path = tmp_path / "dlq.json"
    monkeypatch.setattr(settings, "dlq_backend", "json")
    monkeypatch.setattr(settings, "dlq_path", str(path))

    entry = dlq.record_dead_letter(
        {
            "message_id": "msg-2",
            "reason": "retry_exhausted",
            "payload": {"email_id": "msg-2", "subject": "Hello"},
        },
        path=path,
    )
    claimed = dlq.claim_dead_letter(entry["entry_id"], "dead_letter", "requeued", path=path, requeue_token="token-1")
    assert claimed is not None
    again = dlq.claim_dead_letter(entry["entry_id"], "dead_letter", "requeued", path=path, requeue_token="token-2")
    assert again is None


def test_dlq_api_requeue_processes_once(monkeypatch, tmp_path):
    path = tmp_path / "dlq.json"
    monkeypatch.setattr(settings, "dlq_backend", "json")
    monkeypatch.setattr(settings, "dlq_path", str(path))
    monkeypatch.setattr(dlq, "DEFAULT_DLQ_PATH", path)

    entry = dlq.record_dead_letter(
        {
            "message_id": "msg-3",
            "reason": "retry_exhausted",
            "payload": {"email_id": "msg-3", "gmail_thread_id": "thread-3", "subject": "Hello", "author": "alice@example.com", "to": "me@example.com", "email_thread": "body"},
        },
        path=path,
    )
    calls = []

    async def fake_reprocess(entry_arg, graph):
        calls.append(entry_arg["entry_id"])
        return {"entry_id": entry_arg["entry_id"], "status": "requeued", "run_id": "run-3"}

    monkeypatch.setattr(api, "_reprocess_dead_letter", fake_reprocess)

    with TestClient(app) as client:
        client.app.state.graph = object()
        response = client.post(f"/dlq/{entry['entry_id']}/requeue", headers={"X-Agora-Instance-Role": "owner"})

    assert response.status_code == 200
    assert response.json()["status"] == "requeued"
    assert calls == [entry["entry_id"]]


def test_dlq_api_rejects_requeue_of_a_mailbox_sync_failure(monkeypatch, tmp_path):
    """No email_input exists for a mailbox-level failure — requeuing it would
    invoke the graph with a near-empty payload instead of a clear error."""
    path = tmp_path / "dlq.json"
    monkeypatch.setattr(settings, "dlq_backend", "json")
    monkeypatch.setattr(settings, "dlq_path", str(path))
    monkeypatch.setattr(dlq, "DEFAULT_DLQ_PATH", path)

    entry = dlq.record_dead_letter(
        {
            "entry_id": "mailbox-sync-ceo-email-agent",
            "reason": "mailbox_sync_failure",
            "error": "invalid_scope: Bad Request",
            "payload": {"instance_id": "ceo-email-agent"},
        },
        path=path,
    )

    with TestClient(app) as client:
        client.app.state.graph = object()
        response = client.post(f"/dlq/{entry['entry_id']}/requeue", headers={"X-Agora-Instance-Role": "owner"})

    assert response.status_code == 400
    # Rejected before any claim attempt — still requeueable later once fixed.
    assert dlq.list_dead_letters(path=path, limit=10)[0]["status"] == "dead_letter"


def test_optional_timestamps_are_null_not_empty_strings():
    """Postgres timestamptz rejects '', which silently broke every DLQ write."""
    from src.dlq import _normalize_entry

    entry = _normalize_entry({"message_id": "m1", "reason": "terminal_failure"})
    assert entry["requeued_at"] is None
    assert entry["resolved_at"] is None
    # The required timestamp is still populated.
    assert entry["timestamp"]


def test_optional_timestamps_preserve_a_real_value():
    from datetime import datetime, timezone

    from src.dlq import _normalize_entry

    when = datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc)
    entry = _normalize_entry({"message_id": "m1", "requeued_at": when, "resolved_at": "2026-08-02T09:00:00+00:00"})
    assert entry["requeued_at"] == "2026-08-01T12:30:00+00:00"
    assert entry["resolved_at"] == "2026-08-02T09:00:00+00:00"


def test_a_failed_decision_is_recorded_in_the_dlq(monkeypatch, tmp_path):
    """The failure queue only ever heard from the poller.

    Approvals, rejections and redrafts run through the API, so when one failed
    the operator saw the action break in front of them and the failure page say
    "no DLQ entries". Nothing anywhere recorded it.
    """
    from src import api

    recorded = []
    monkeypatch.setattr(api, "record_dead_letter", lambda entry: recorded.append(entry))

    api._record_decision_failure(
        "run-77",
        "approve",
        RuntimeError("upstream exploded"),
        {"email_id": "msg-77"},
    )

    assert len(recorded) == 1
    entry = recorded[0]
    assert entry["message_id"] == "msg-77"
    assert entry["reason"] == "decision_failed:approve"
    assert "upstream exploded" in entry["error"]
    assert entry["payload"]["run_id"] == "run-77"


def test_recording_a_decision_failure_never_raises(monkeypatch):
    # A DLQ write that throws must not turn one failure into a different one.
    from src import api

    def _explode(_entry):
        raise RuntimeError("dlq backend down")

    monkeypatch.setattr(api, "record_dead_letter", _explode)

    api._record_decision_failure("run-78", "reject", ValueError("boom"), None)


def test_a_decision_failure_without_a_run_record_still_files_something(monkeypatch):
    # No record means no Gmail id; the run id is the only handle left, and an
    # entry keyed on it beats no entry at all.
    from src import api

    recorded = []
    monkeypatch.setattr(api, "record_dead_letter", lambda entry: recorded.append(entry))

    api._record_decision_failure("run-79", "regenerate draft", RuntimeError("x"), None)

    assert recorded[0]["message_id"] == "run-79"
