from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from src import cost_tracker, retention, run_registry, trace
from src.config import settings


def _insert_checkpoint_rows(path, run_id: str):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE checkpoints (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT, type TEXT, checkpoint BLOB, metadata BLOB, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))"
    )
    conn.execute(
        "CREATE TABLE writes (thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '', checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL, idx INTEGER NOT NULL, channel TEXT NOT NULL, type TEXT, value BLOB, PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx))"
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


def test_retention_dry_run_and_execute_preserve_audit(monkeypatch, tmp_path):
    run_path = tmp_path / "run_index.json"
    cost_path = tmp_path / "costs.jsonl"
    trace_path = tmp_path / "traces.jsonl"
    checkpoints_db = tmp_path / "checkpoints.db"
    retention_cfg = tmp_path / "retention.yaml"
    audit_log = tmp_path / "audit.log"
    audit_log.write_text("audit-entry\n", encoding="utf-8")

    monkeypatch.setattr(settings, "run_registry_backend", "json")
    monkeypatch.setattr(run_registry, "DEFAULT_RUN_INDEX", run_path)
    monkeypatch.setattr(settings, "cost_backend", "json")
    monkeypatch.setattr(settings, "trace_backend", "json")
    monkeypatch.setattr(settings, "storage_backend", "sqlite")
    monkeypatch.setattr(settings, "retention_path", str(retention_cfg))
    monkeypatch.setattr(settings, "checkpoints_db", str(checkpoints_db))
    monkeypatch.setattr(settings, "costs_path", str(cost_path))
    monkeypatch.setattr(settings, "traces_path", str(trace_path))

    retention.save_retention_settings(retention.RetentionSettings(retention_days=30))

    old_created = "2026-05-01T10:00:00+00:00"
    fresh_created = "2026-07-01T10:00:00+00:00"
    run_registry.upsert_run(
        "run-old",
        "completed",
        email_input={"subject": "old"},
        created_at=old_created,
        path=run_path,
    )
    run_registry.upsert_run(
        "run-new",
        "completed",
        email_input={"subject": "new"},
        created_at=fresh_created,
        path=run_path,
    )
    cost_tracker.record_cost(
        {"run_id": "run-old", "node": "triage", "model": "m", "input_tokens": 1, "output_tokens": 2},
        path=cost_path,
    )
    cost_tracker.record_cost(
        {"run_id": "run-new", "node": "triage", "model": "m", "input_tokens": 1, "output_tokens": 2},
        path=cost_path,
    )
    trace.record_trace({"run_id": "run-old", "node": "triage", "status": "ok", "latency_ms": 10}, path=trace_path)
    trace.record_trace({"run_id": "run-new", "node": "triage", "status": "ok", "latency_ms": 10}, path=trace_path)
    _insert_checkpoint_rows(checkpoints_db, "run-old")
    conn = sqlite3.connect(checkpoints_db)
    conn.execute("INSERT INTO checkpoints(thread_id, checkpoint_ns, checkpoint_id) VALUES ('run-new', '', 'cp-run-new')")
    conn.execute("INSERT INTO writes(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel) VALUES ('run-new', '', 'cp-run-new', 'task', 0, 'messages')")
    conn.commit()
    conn.close()

    fixed_now = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    dry_run = retention.preview_retention(now=fixed_now)
    assert dry_run["counts"] == {
        "runs": 1,
        "cost_entries": 1,
        "trace_entries": 1,
        "checkpoints": 1,
        "checkpoint_writes": 1,
    }
    assert run_registry.get_run("run-old", path=run_path) is not None

    executed = retention.run_retention(now=fixed_now)
    # run_retention also sweeps notifications on its own always-on age window,
    # independent of the run/cost/trace retention_days preview above.
    assert executed["deleted"] == {**dry_run["counts"], "notifications": 0}
    assert run_registry.get_run("run-old", path=run_path) is None
    assert run_registry.get_run("run-new", path=run_path) is not None
    assert [row["run_id"] for row in cost_tracker.list_costs(path=cost_path, limit=10)] == ["run-new"]
    assert [row["run_id"] for row in trace.list_traces(path=trace_path, limit=10)] == ["run-new"]
    assert audit_log.read_text(encoding="utf-8") == "audit-entry\n"


def test_retention_disabled_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "retention_path", str(tmp_path / "retention.yaml"))
    retention.save_retention_settings(retention.RetentionSettings(retention_days=0))
    preview = retention.preview_retention(now=datetime(2026, 7, 11, tzinfo=timezone.utc))
    assert preview["enabled"] is False
    assert preview["counts"]["runs"] == 0
