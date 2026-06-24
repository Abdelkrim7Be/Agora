from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.config import SERVICE_ROOT, settings
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
)

DEFAULT_RUN_INDEX = SERVICE_ROOT / "logs" / "run_index.json"

# Cap the local JSON index so a long-running dev poller can't grow it unbounded.
# Production Phase 4 deployments use the Postgres backend instead.
MAX_RUNS = 1000


def _path(path: str | Path | None = None) -> Path:
    if path is None:
        return DEFAULT_RUN_INDEX
    p = Path(path)
    return p if p.is_absolute() else SERVICE_ROOT / p


def selected_run_registry_backend(path: str | Path | None = None) -> str:
    if path is not None:
        return "json"
    backend = settings.run_registry_backend.lower().strip()
    if backend not in {"json", "postgres"}:
        raise RuntimeError(f"Unsupported AGENT_RUN_REGISTRY_BACKEND: {settings.run_registry_backend}")
    if backend == "postgres" and not settings.database_url:
        raise RuntimeError("DATABASE_URL is required when AGENT_RUN_REGISTRY_BACKEND=postgres")
    return backend


def _read(path: Path) -> dict:
    if not path.is_file():
        return {"runs": []}
    return json.loads(path.read_text())


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _record(
    run_id: str,
    status: str,
    email_input: dict | None,
    classification: str | None,
    pending_action: list | None,
    user_id: str | None,
    agent_instance_id: str | None,
) -> dict:
    email_input = email_input or {}
    resolved_user_id = normalize_user_id(user_id or current_user_id())
    resolved_instance_id = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    return {
        "user_id": resolved_user_id,
        "agent_instance_id": resolved_instance_id,
        "run_id": run_id,
        "status": status,
        "classification": classification,
        "pending_action": pending_action,
        "subject": email_input.get("subject"),
        "author": email_input.get("author"),
        "email_id": email_input.get("email_id"),
        "gmail_thread_id": email_input.get("gmail_thread_id"),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _json_upsert(record: dict, path: str | Path | None = None) -> dict:
    index_path = _path(path)
    data = _read(index_path)
    runs = data.setdefault("runs", [])
    existing = next((r for r in runs if r.get("run_id") == record["run_id"]), None)
    if existing:
        for key, value in record.items():
            if value is not None or key in {
                "status",
                "pending_action",
                "updated_at",
                "user_id",
                "agent_instance_id",
            }:
                existing[key] = value
        saved = existing
    else:
        runs.append(record)
        saved = record
    runs.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    if len(runs) > MAX_RUNS:
        data["runs"] = runs[:MAX_RUNS]
    _write(index_path, data)
    return saved


def _json_list(
    status: str | None,
    path: str | Path | None,
    user_id: str | None,
    agent_instance_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    runs = _read(_path(path)).get("runs", [])
    if user_id is not None:
        resolved_user_id = normalize_user_id(user_id)
        runs = [r for r in runs if normalize_user_id(r.get("user_id")) == resolved_user_id]
    if agent_instance_id is not None:
        resolved_instance_id = normalize_agent_instance_id(agent_instance_id)
        runs = [
            r for r in runs
            if normalize_agent_instance_id(r.get("agent_instance_id")) == resolved_instance_id
        ]
    if status:
        runs = [r for r in runs if r.get("status") == status]
    if limit is not None:
        return runs[offset:offset + limit]
    return runs


def _json_get(
    run_id: str,
    path: str | Path | None,
    user_id: str | None,
    agent_instance_id: str | None,
) -> dict | None:
    runs = _json_list(
        status=None,
        path=path,
        user_id=user_id,
        agent_instance_id=agent_instance_id,
    )
    return next((r for r in runs if r.get("run_id") == run_id), None)


def _connect():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Postgres run registry requires psycopg.") from exc
    return psycopg.connect(settings.database_url)


def setup_run_registry() -> None:
    if selected_run_registry_backend() != "postgres":
        return
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_instance_id TEXT NOT NULL DEFAULT 'default-email-agent',
                    status TEXT NOT NULL,
                    classification TEXT,
                    pending_action JSONB,
                    subject TEXT,
                    author TEXT,
                    email_id TEXT,
                    gmail_thread_id TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "ALTER TABLE agent_runs "
                "ADD COLUMN IF NOT EXISTS agent_instance_id TEXT NOT NULL DEFAULT 'default-email-agent'"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS agent_runs_user_instance_status_updated_idx "
                "ON agent_runs (user_id, agent_instance_id, status, updated_at DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS agent_runs_user_instance_updated_idx "
                "ON agent_runs (user_id, agent_instance_id, updated_at DESC)"
            )


def _postgres_row(row: dict[str, Any]) -> dict:
    updated = row.get("updated_at")
    if hasattr(updated, "isoformat"):
        updated = updated.isoformat(timespec="seconds")
    return {**row, "updated_at": updated}


def _postgres_upsert(record: dict) -> dict:
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    setup_run_registry()
    params = {
        **record,
        "pending_action": Jsonb(record["pending_action"])
        if record["pending_action"] is not None
        else None,
    }
    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO agent_runs (
                    run_id, user_id, agent_instance_id, status, classification,
                    pending_action, subject, author, email_id, gmail_thread_id, updated_at
                ) VALUES (
                    %(run_id)s, %(user_id)s, %(agent_instance_id)s, %(status)s,
                    %(classification)s, %(pending_action)s, %(subject)s, %(author)s,
                    %(email_id)s, %(gmail_thread_id)s, %(updated_at)s
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    agent_instance_id = EXCLUDED.agent_instance_id,
                    status = EXCLUDED.status,
                    classification = COALESCE(EXCLUDED.classification, agent_runs.classification),
                    pending_action = EXCLUDED.pending_action,
                    subject = COALESCE(EXCLUDED.subject, agent_runs.subject),
                    author = COALESCE(EXCLUDED.author, agent_runs.author),
                    email_id = COALESCE(EXCLUDED.email_id, agent_runs.email_id),
                    gmail_thread_id = COALESCE(EXCLUDED.gmail_thread_id, agent_runs.gmail_thread_id),
                    updated_at = EXCLUDED.updated_at
                RETURNING run_id, user_id, agent_instance_id, status, classification,
                    pending_action, subject, author, email_id, gmail_thread_id, updated_at
                """,
                params,
            )
            return _postgres_row(cur.fetchone())


def _postgres_list(
    status: str | None,
    user_id: str | None,
    agent_instance_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    from psycopg.rows import dict_row

    setup_run_registry()
    resolved_user_id = normalize_user_id(user_id) if user_id is not None else None
    clauses = []
    params: dict[str, Any] = {}
    if resolved_user_id is not None:
        clauses.append("user_id = %(user_id)s")
        params["user_id"] = resolved_user_id
    if agent_instance_id is not None:
        clauses.append("agent_instance_id = %(agent_instance_id)s")
        params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
    if status:
        clauses.append("status = %(status)s")
        params["status"] = status
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params["limit"] = MAX_RUNS if limit is None else limit
    params["offset"] = max(offset, 0)
    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT run_id, user_id, agent_instance_id, status, classification,
                    pending_action, subject, author, email_id, gmail_thread_id, updated_at
                FROM agent_runs
                """
                + where
                + " ORDER BY updated_at DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            return [_postgres_row(row) for row in cur.fetchall()]


def _postgres_get(
    run_id: str,
    user_id: str | None,
    agent_instance_id: str | None,
) -> dict | None:
    from psycopg.rows import dict_row

    setup_run_registry()
    resolved_user_id = normalize_user_id(user_id) if user_id is not None else None
    clauses = ["run_id = %(run_id)s"]
    params: dict[str, Any] = {"run_id": run_id}
    if resolved_user_id is not None:
        clauses.append("user_id = %(user_id)s")
        params["user_id"] = resolved_user_id
    if agent_instance_id is not None:
        clauses.append("agent_instance_id = %(agent_instance_id)s")
        params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT run_id, user_id, agent_instance_id, status, classification,
                    pending_action, subject, author, email_id, gmail_thread_id, updated_at
                FROM agent_runs
                WHERE
                """
                + " AND ".join(clauses),
                params,
            )
            row = cur.fetchone()
            return _postgres_row(row) if row else None


def upsert_run(
    run_id: str,
    status: str,
    email_input: dict | None = None,
    classification: str | None = None,
    pending_action: list | None = None,
    path: str | Path | None = None,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> dict:
    record = _record(
        run_id,
        status,
        email_input,
        classification,
        pending_action,
        user_id,
        agent_instance_id,
    )
    if selected_run_registry_backend(path) == "postgres":
        return _postgres_upsert(record)
    return _json_upsert(record, path=path)


def list_runs(
    status: str | None = None,
    path: str | Path | None = None,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    if selected_run_registry_backend(path) == "postgres":
        return _postgres_list(
            status=status,
            user_id=user_id,
            agent_instance_id=agent_instance_id,
            limit=limit,
            offset=offset,
        )
    return _json_list(
        status=status,
        path=path,
        user_id=user_id,
        agent_instance_id=agent_instance_id,
        limit=limit,
        offset=offset,
    )


def get_run(
    run_id: str,
    path: str | Path | None = None,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> dict | None:
    if selected_run_registry_backend(path) == "postgres":
        return _postgres_get(
            run_id=run_id,
            user_id=user_id,
            agent_instance_id=agent_instance_id,
        )
    return _json_get(
        run_id=run_id,
        path=path,
        user_id=user_id,
        agent_instance_id=agent_instance_id,
    )


# Statuses where the email is still awaiting a human and is left UNREAD on purpose.
ACTIVE_RUN_STATUSES = ("pending_approval", "security_hold")


def find_run_by_email(
    email_id: str,
    path: str | Path | None = None,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> dict | None:
    """Return the most recent run for a Gmail message id, or None.

    Lets the poller avoid reprocessing an email that already has a run: a pending
    or held run must not spawn duplicates every cycle, and a resolved run means the
    message was already handled. list_runs is newest-first, so the first match wins.
    """
    if not email_id:
        return None
    for record in list_runs(path=path, user_id=user_id, agent_instance_id=agent_instance_id):
        if record.get("email_id") == email_id:
            return record
    return None
