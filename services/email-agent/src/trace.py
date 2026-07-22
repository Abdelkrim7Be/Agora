from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from src.config import SERVICE_ROOT, settings
from src.postgres import tenant_connection
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
)

DEFAULT_TRACE_PATH = SERVICE_ROOT / "logs" / "llm_traces.jsonl"


def _path(path: str | Path | None = None) -> Path:
    if path is None:
        path = settings.traces_path or DEFAULT_TRACE_PATH
    p = Path(path)
    return p if p.is_absolute() else SERVICE_ROOT / p


def selected_trace_backend(path: str | Path | None = None) -> str:
    if path is not None:
        return "json"
    backend = settings.trace_backend.lower().strip()
    if backend not in {"json", "postgres"}:
        raise RuntimeError(f"Unsupported AGENT_TRACE_BACKEND: {settings.trace_backend}")
    if backend == "postgres" and not settings.database_url:
        raise RuntimeError("DATABASE_URL is required when AGENT_TRACE_BACKEND=postgres")
    return backend


def _connect():
    return tenant_connection()


def setup_trace_store() -> None:
    if selected_trace_backend() != "postgres":
        return
    # Postgres schema is owned by Alembic migrations. JSON dev path stays unchanged.
    return


def _normalize_entry(entry: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    input_tokens = int(entry.get("input_tokens") or 0)
    output_tokens = int(entry.get("output_tokens") or 0)
    return {
        "event_id": str(entry.get("event_id") or uuid.uuid4()),
        "timestamp": str(entry.get("timestamp") or entry.get("finished_at") or now),
        "started_at": str(entry.get("started_at") or now),
        "finished_at": str(entry.get("finished_at") or entry.get("timestamp") or now),
        "user_id": normalize_user_id(entry.get("user_id") or current_user_id()),
        "agent_instance_id": normalize_agent_instance_id(
            entry.get("agent_instance_id") or current_agent_instance_id()
        ),
        "run_id": str(entry.get("run_id") or ""),
        "node": str(entry.get("node") or "unknown"),
        "status": str(entry.get("status") or "ok"),
        "latency_ms": int(entry.get("latency_ms") or 0),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(entry.get("total_tokens") or input_tokens + output_tokens),
        "cost_eur": float(entry.get("cost_eur") or 0.0),
        "error": str(entry.get("error") or ""),
    }


def _json_append(entry: dict, path: str | Path | None = None) -> dict:
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def _json_entries(path: str | Path | None = None) -> list[dict]:
    target = _path(path)
    if not target.is_file():
        return []
    entries: list[dict] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entries.append(json.loads(line))
    return entries


def _pg_insert(entry: dict) -> dict:
    setup_trace_store()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO llm_traces (
                    event_id, timestamp, started_at, finished_at, user_id,
                    agent_instance_id, run_id, node, status, latency_ms,
                    input_tokens, output_tokens, total_tokens, cost_eur, error
                ) VALUES (
                    %(event_id)s, %(timestamp)s, %(started_at)s, %(finished_at)s,
                    %(user_id)s, %(agent_instance_id)s, %(run_id)s, %(node)s,
                    %(status)s, %(latency_ms)s, %(input_tokens)s, %(output_tokens)s,
                    %(total_tokens)s, %(cost_eur)s, %(error)s
                )
                ON CONFLICT (event_id) DO NOTHING
                """,
                entry,
            )
    return entry


def record_trace(entry: dict, path: str | Path | None = None) -> dict:
    normalized = _normalize_entry(entry)
    if selected_trace_backend(path) == "postgres":
        return _pg_insert(normalized)
    return _json_append(normalized, path=path)


def _matches(
    entry: dict,
    run_id: str | None,
    user_id: str | None,
    agent_instance_id: str | None,
) -> bool:
    if run_id is not None and entry.get("run_id") != run_id:
        return False
    if user_id is not None and normalize_user_id(entry.get("user_id")) != normalize_user_id(user_id):
        return False
    if agent_instance_id is not None and normalize_agent_instance_id(entry.get("agent_instance_id")) != normalize_agent_instance_id(agent_instance_id):
        return False
    return True


def _pg_entries(
    run_id: str | None,
    user_id: str | None,
    agent_instance_id: str | None,
    limit: int | None = None,
) -> list[dict]:
    from psycopg.rows import dict_row

    setup_trace_store()
    clauses = []
    params: dict[str, Any] = {}
    if run_id is not None:
        clauses.append("run_id = %(run_id)s")
        params["run_id"] = run_id
    if user_id is not None:
        clauses.append("user_id = %(user_id)s")
        params["user_id"] = normalize_user_id(user_id)
    if agent_instance_id is not None:
        clauses.append("agent_instance_id = %(agent_instance_id)s")
        params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
    params["limit"] = 500 if limit is None else max(1, min(limit, 5000))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT event_id, timestamp, started_at, finished_at, user_id,
                    agent_instance_id, run_id, node, status, latency_ms,
                    input_tokens, output_tokens, total_tokens, cost_eur, error
                FROM llm_traces
                """
                + where
                + " ORDER BY timestamp ASC LIMIT %(limit)s",
                params,
            )
            rows = []
            for row in cur.fetchall():
                normalized = {}
                for key, value in row.items():
                    normalized[key] = (
                        value.isoformat(timespec="milliseconds")
                        if hasattr(value, "isoformat")
                        else value
                    )
                rows.append(normalized)
            return rows


def list_traces(
    run_id: str | None = None,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
    limit: int = 500,
    path: str | Path | None = None,
) -> list[dict]:
    limit = max(1, min(limit, 5000))
    if selected_trace_backend(path) == "postgres":
        return _pg_entries(run_id, user_id, agent_instance_id, limit=limit)
    entries = [
        entry
        for entry in _json_entries(path)
        if _matches(entry, run_id, user_id, agent_instance_id)
    ]
    entries.sort(key=lambda entry: entry.get("timestamp", ""))
    return entries[:limit]


def _zero_totals() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_eur": 0.0,
    }


def count_traces_for_runs(
    run_ids: list[str],
    agent_instance_id: str | None = None,
    path: str | Path | None = None,
) -> int:
    run_ids = [str(run_id) for run_id in run_ids if run_id]
    if not run_ids:
        return 0
    if selected_trace_backend(path) == "postgres":
        setup_trace_store()
        clauses = ["run_id = ANY(%(run_ids)s)"]
        params: dict[str, Any] = {"run_ids": run_ids}
        if agent_instance_id is not None:
            clauses.append("agent_instance_id = %(agent_instance_id)s")
            params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM llm_traces WHERE " + " AND ".join(clauses),
                    params,
                )
                row = cur.fetchone()
                return int(row[0] if row else 0)
    count = 0
    for entry in _json_entries(path):
        if entry.get("run_id") not in run_ids:
            continue
        if agent_instance_id is not None and normalize_agent_instance_id(entry.get("agent_instance_id")) != normalize_agent_instance_id(agent_instance_id):
            continue
        count += 1
    return count


def delete_traces_for_runs(
    run_ids: list[str],
    agent_instance_id: str | None = None,
    path: str | Path | None = None,
) -> int:
    run_ids = [str(run_id) for run_id in run_ids if run_id]
    if not run_ids:
        return 0
    if selected_trace_backend(path) == "postgres":
        setup_trace_store()
        clauses = ["run_id = ANY(%(run_ids)s)"]
        params: dict[str, Any] = {"run_ids": run_ids}
        if agent_instance_id is not None:
            clauses.append("agent_instance_id = %(agent_instance_id)s")
            params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM llm_traces WHERE " + " AND ".join(clauses),
                    params,
                )
                return cur.rowcount or 0
    target = _path(path)
    if not target.is_file():
        return 0
    kept: list[dict] = []
    deleted = 0
    for entry in _json_entries(path):
        matches_run = entry.get("run_id") in run_ids
        matches_instance = (
            agent_instance_id is None
            or normalize_agent_instance_id(entry.get("agent_instance_id")) == normalize_agent_instance_id(agent_instance_id)
        )
        if matches_run and matches_instance:
            deleted += 1
        else:
            kept.append(entry)
    target.write_text("".join(json.dumps(entry, sort_keys=True) + "\n" for entry in kept), encoding="utf-8")
    return deleted
