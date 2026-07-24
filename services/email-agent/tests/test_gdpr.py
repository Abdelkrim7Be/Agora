from __future__ import annotations

import sqlite3

from src import contacts, cost_tracker, gdpr, run_registry, token_store, trace
from src.config import settings
from src.contacts import Contact
from src.token_store import has_stored_token, token_file_for_user


def _insert_checkpoint_rows(path, run_id: str):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS checkpoints (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, metadata BLOB, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS writes (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL, type TEXT, value BLOB, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx))"
    )
    conn.execute(
        "INSERT INTO checkpoints(thread_id, checkpoint_ns, checkpoint_id) VALUES (?, '', ?)",
        (run_id, f"cp-{run_id}"),
    )
    conn.execute(
        "INSERT INTO writes(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel) VALUES (?, '', ?, 'task', 0, 'messages')",
        (run_id, f"cp-{run_id}"),
    )
    conn.commit()
    conn.close()


def _setup_paths(monkeypatch, tmp_path):
    run_path = tmp_path / "run_index.json"
    cost_path = tmp_path / "costs.jsonl"
    trace_path = tmp_path / "traces.jsonl"
    checkpoints_db = tmp_path / "checkpoints.db"
    contacts_path = tmp_path / "contacts.yaml"
    token_path = tmp_path / "token.json"

    monkeypatch.setattr(settings, "run_registry_backend", "json")
    monkeypatch.setattr(run_registry, "DEFAULT_RUN_INDEX", run_path)
    monkeypatch.setattr(settings, "cost_backend", "json")
    monkeypatch.setattr(settings, "trace_backend", "json")
    monkeypatch.setattr(settings, "storage_backend", "sqlite")
    monkeypatch.setattr(settings, "checkpoints_db", str(checkpoints_db))
    monkeypatch.setattr(settings, "costs_path", str(cost_path))
    monkeypatch.setattr(settings, "traces_path", str(trace_path))
    monkeypatch.setattr(contacts, "DEFAULT_CONTACTS_PATH", contacts_path)
    monkeypatch.setattr(settings, "gmail_token_path", str(token_path))

    return {
        "run_path": run_path,
        "cost_path": cost_path,
        "trace_path": trace_path,
        "checkpoints_db": checkpoints_db,
    }


def test_erase_subject_removes_contact_runs_costs_traces_and_checkpoints(monkeypatch, tmp_path):
    paths = _setup_paths(monkeypatch, tmp_path)

    contacts.upsert_contact(Contact(email="alice@example.com", audience="client"))
    contacts.upsert_contact(Contact(email="bob@example.com", audience="client"))

    run_registry.upsert_run(
        "run-alice",
        "completed",
        email_input={"subject": "hi", "author": "Alice <alice@example.com>"},
        path=paths["run_path"],
    )
    run_registry.upsert_run(
        "run-bob",
        "completed",
        email_input={"subject": "hi", "author": "Bob <bob@example.com>"},
        path=paths["run_path"],
    )

    cost_tracker.record_cost(
        {"run_id": "run-alice", "node": "triage", "model": "m", "input_tokens": 1, "output_tokens": 2},
        path=paths["cost_path"],
    )
    cost_tracker.record_cost(
        {"run_id": "run-bob", "node": "triage", "model": "m", "input_tokens": 1, "output_tokens": 2},
        path=paths["cost_path"],
    )
    trace.record_trace({"run_id": "run-alice", "node": "triage", "status": "ok", "latency_ms": 5}, path=paths["trace_path"])
    trace.record_trace({"run_id": "run-bob", "node": "triage", "status": "ok", "latency_ms": 5}, path=paths["trace_path"])
    _insert_checkpoint_rows(paths["checkpoints_db"], "run-alice")
    _insert_checkpoint_rows(paths["checkpoints_db"], "run-bob")

    preview = gdpr.preview_erasure("alice@example.com")
    assert preview["contact_found"] is True
    assert preview["run_ids"] == ["run-alice"]
    assert preview["counts"] == {
        "runs": 1,
        "cost_entries": 1,
        "trace_entries": 1,
        "checkpoints": 1,
        "checkpoint_writes": 1,
    }

    result = gdpr.erase_subject("alice@example.com")

    assert result["contact_deleted"] is True
    assert result["token_revoked"] is False
    assert result["run_ids"] == ["run-alice"]
    assert result["deleted"] == preview["counts"]

    assert contacts.get_contact("alice@example.com") is None
    assert contacts.get_contact("bob@example.com") is not None
    assert run_registry.get_run("run-alice", path=paths["run_path"]) is None
    assert run_registry.get_run("run-bob", path=paths["run_path"]) is not None
    assert [row["run_id"] for row in cost_tracker.list_costs(path=paths["cost_path"], limit=10)] == ["run-bob"]
    assert [row["run_id"] for row in trace.list_traces(path=paths["trace_path"], limit=10)] == ["run-bob"]


def test_erase_subject_with_no_matching_data_is_a_clean_noop(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)

    result = gdpr.erase_subject("nobody@example.com")

    assert result["contact_deleted"] is False
    assert result["token_revoked"] is False
    assert result["run_ids"] == []
    assert result["deleted"] == {
        "runs": 0,
        "cost_entries": 0,
        "trace_entries": 0,
        "checkpoints": 0,
        "checkpoint_writes": 0,
    }


def test_erase_subject_revokes_owner_token_only_when_requested(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    token_file_for_user().write_text("{}")

    not_revoked = gdpr.erase_subject("owner@example.com", revoke_owner_token=False)
    assert not_revoked["token_revoked"] is False
    assert has_stored_token() is True

    revoked = gdpr.erase_subject("owner@example.com", revoke_owner_token=True)
    assert revoked["token_revoked"] is True
    assert has_stored_token() is False


def test_preview_erasure_matches_author_case_insensitively(monkeypatch, tmp_path):
    paths = _setup_paths(monkeypatch, tmp_path)

    run_registry.upsert_run(
        "run-mixed-case",
        "completed",
        email_input={"subject": "hi", "author": "Alice <ALICE@Example.com>"},
        path=paths["run_path"],
    )

    preview = gdpr.preview_erasure("alice@example.com")
    assert preview["run_ids"] == ["run-mixed-case"]
