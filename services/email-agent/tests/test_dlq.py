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
