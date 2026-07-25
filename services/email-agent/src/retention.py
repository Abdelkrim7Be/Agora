from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from pathlib import Path

from pydantic import BaseModel, Field
import yaml

from src.config import SERVICE_ROOT, settings
from src.cost_tracker import count_costs_for_runs, delete_costs_for_runs
from src.instance_config import read_instance_text, write_instance_text
from src.postgres import tenant_connection
from src.run_registry import delete_runs, list_runs_before
from src.storage import selected_storage_backend
from src.tenant import current_agent_instance_id
from src.trace import count_traces_for_runs, delete_traces_for_runs

DEFAULT_RETENTION_PATH = SERVICE_ROOT / "retention.yaml"


class RetentionSettings(BaseModel):
    retention_days: int = Field(default=0, ge=0, le=3650)


def _settings_path() -> Path:
    return Path(settings.retention_path or DEFAULT_RETENTION_PATH)


def load_retention_settings(agent_instance_id: str | None = None) -> RetentionSettings:
    raw = read_instance_text("retention", _settings_path(), agent_instance_id=agent_instance_id)
    data = yaml.safe_load(raw) or {}
    return RetentionSettings(**data)


def save_retention_settings(config: RetentionSettings, agent_instance_id: str | None = None) -> RetentionSettings:
    payload = yaml.safe_dump(config.model_dump(), sort_keys=False)
    write_instance_text("retention", payload, _settings_path(), agent_instance_id=agent_instance_id)
    return config


def _zero_counts() -> dict[str, int]:
    return {
        "runs": 0,
        "cost_entries": 0,
        "trace_entries": 0,
        "checkpoints": 0,
        "checkpoint_writes": 0,
    }


def _sqlite_count(table: str, run_ids: list[str]) -> int:
    db_path = Path(settings.checkpoints_db)
    if not db_path.is_absolute():
        db_path = SERVICE_ROOT / db_path
    if not db_path.is_file() or not run_ids:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" for _ in run_ids)
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE thread_id IN ({placeholders})",
            run_ids,
        ).fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def _sqlite_delete(table: str, run_ids: list[str]) -> int:
    db_path = Path(settings.checkpoints_db)
    if not db_path.is_absolute():
        db_path = SERVICE_ROOT / db_path
    if not db_path.is_file() or not run_ids:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" for _ in run_ids)
        cur = conn.execute(
            f"DELETE FROM {table} WHERE thread_id IN ({placeholders})",
            run_ids,
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def _postgres_count(table: str, run_ids: list[str]) -> int:
    with tenant_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE thread_id = ANY(%s)",
                (run_ids,),
            )
            row = cur.fetchone()
            return int(row[0] if row else 0)


def _postgres_delete(table: str, run_ids: list[str]) -> int:
    with tenant_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {table} WHERE thread_id = ANY(%s)",
                (run_ids,),
            )
            return cur.rowcount or 0


def _checkpoint_counts(run_ids: list[str]) -> dict[str, int]:
    if not run_ids:
        return {"checkpoints": 0, "checkpoint_writes": 0}
    backend = selected_storage_backend()
    if backend == "postgres":
        return {
            "checkpoints": _postgres_count("checkpoints", run_ids),
            "checkpoint_writes": _postgres_count("writes", run_ids),
        }
    return {
        "checkpoints": _sqlite_count("checkpoints", run_ids),
        "checkpoint_writes": _sqlite_count("writes", run_ids),
    }


def _delete_checkpoints(run_ids: list[str]) -> dict[str, int]:
    if not run_ids:
        return {"checkpoints": 0, "checkpoint_writes": 0}
    backend = selected_storage_backend()
    if backend == "postgres":
        return {
            "checkpoints": _postgres_delete("checkpoints", run_ids),
            "checkpoint_writes": _postgres_delete("writes", run_ids),
        }
    return {
        "checkpoints": _sqlite_delete("checkpoints", run_ids),
        "checkpoint_writes": _sqlite_delete("writes", run_ids),
    }


def preview_retention(now: datetime | None = None) -> dict:
    config = load_retention_settings()
    if config.retention_days <= 0:
        return {
            "enabled": False,
            "retention_days": 0,
            "cutoff": None,
            "counts": _zero_counts(),
            "sample_run_ids": [],
            "audit_retained": True,
            "agent_instance_id": current_agent_instance_id(),
        }
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = now - timedelta(days=config.retention_days)
    runs = list_runs_before(cutoff, agent_instance_id=current_agent_instance_id())
    run_ids = [run["run_id"] for run in runs if run.get("run_id")]
    checkpoint_counts = _checkpoint_counts(run_ids)
    counts = {
        "runs": len(run_ids),
        "cost_entries": count_costs_for_runs(run_ids, agent_instance_id=current_agent_instance_id()),
        "trace_entries": count_traces_for_runs(run_ids, agent_instance_id=current_agent_instance_id()),
        **checkpoint_counts,
    }
    return {
        "enabled": True,
        "retention_days": config.retention_days,
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "counts": counts,
        "sample_run_ids": run_ids[:20],
        "audit_retained": True,
        "agent_instance_id": current_agent_instance_id(),
    }


def run_retention(now: datetime | None = None) -> dict:
    from src.notification_store import prune_notifications

    # Independent of the run/cost/trace retention window above — notifications
    # have their own always-on age limit (AGENT_NOTIFICATION_RETENTION_DAYS).
    notifications_pruned = prune_notifications(settings.notification_retention_days)

    plan = preview_retention(now=now)
    deleted = _zero_counts()
    deleted["notifications"] = notifications_pruned
    run_ids = list(plan.get("sample_run_ids") or [])
    if not plan.get("enabled"):
        plan["deleted"] = deleted
        return plan
    runs = list_runs_before(
        datetime.fromisoformat(plan["cutoff"].replace("Z", "+00:00")),
        agent_instance_id=current_agent_instance_id(),
    )
    run_ids = [run["run_id"] for run in runs if run.get("run_id")]
    if not run_ids:
        plan["sample_run_ids"] = []
        plan["deleted"] = deleted
        return plan
    checkpoint_deleted = _delete_checkpoints(run_ids)
    deleted["trace_entries"] = delete_traces_for_runs(run_ids, agent_instance_id=current_agent_instance_id())
    deleted["cost_entries"] = delete_costs_for_runs(run_ids, agent_instance_id=current_agent_instance_id())
    deleted["runs"] = delete_runs(run_ids, agent_instance_id=current_agent_instance_id())
    deleted.update(checkpoint_deleted)
    plan["sample_run_ids"] = run_ids[:20]
    plan["deleted"] = deleted
    return plan
