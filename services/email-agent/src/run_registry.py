from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
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
_json_transition_lock = Lock()


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



def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record(
    run_id: str,
    status: str,
    email_input: dict | None,
    classification: str | None,
    pending_action: list | None,
    user_id: str | None,
    agent_instance_id: str | None,
    created_at: str | None,
    decision: str | None,
    decision_at: str | None,
) -> dict:
    email_input = email_input or {}
    resolved_user_id = normalize_user_id(user_id or current_user_id())
    resolved_instance_id = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
        "category": email_input.get("category"),
        "category_display_name": email_input.get("category_display_name"),
        # priority is NOT NULL in Postgres; callers may pass an explicit None
        # (e.g. security_hold / notify runs with no matched category), so coerce.
        "priority": email_input.get("priority") or "normal",
        "template": email_input.get("template"),
        "workflow_owner": email_input.get("workflow_owner"),
        "workflow_approver": email_input.get("workflow_approver"),
        "workflow_route_to": email_input.get("workflow_route_to") or [],
        "workflow_dept": email_input.get("workflow_dept"),
        "assignee": email_input.get("assignee"),
        "error": email_input.get("error"),
        "created_at": created_at or now,
        "decision": decision,
        "decision_at": decision_at,
        "updated_at": now,
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
                "category",
                "category_display_name",
                "priority",
                "template",
                "workflow_owner",
                "workflow_approver",
                "workflow_route_to",
                "workflow_dept",
                "assignee",
                "error",
            }:
                existing[key] = value
        if not existing.get("created_at"):
            existing["created_at"] = record.get("created_at") or existing.get("updated_at")
        if record.get("decision") is not None:
            existing["decision"] = record["decision"]
        if record.get("decision_at") is not None:
            existing["decision_at"] = record["decision_at"]
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
    # Postgres schema is owned by Alembic migrations. SQLite/json dev paths stay unchanged.
    return


def _postgres_row(row: dict[str, Any]) -> dict:
    created = row.get("created_at")
    decision_at = row.get("decision_at")
    updated = row.get("updated_at")
    if hasattr(created, "isoformat"):
        created = created.isoformat(timespec="seconds")
    if hasattr(decision_at, "isoformat"):
        decision_at = decision_at.isoformat(timespec="seconds")
    if hasattr(updated, "isoformat"):
        updated = updated.isoformat(timespec="seconds")
    return {
        **row,
        "created_at": created or updated,
        "decision_at": decision_at,
        "updated_at": updated,
    }


def _postgres_upsert(record: dict) -> dict:
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    setup_run_registry()
    params = {
        **record,
        "pending_action": Jsonb(record["pending_action"])
        if record["pending_action"] is not None
        else None,
        "workflow_route_to": Jsonb(record.get("workflow_route_to") or []),
    }
    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO agent_runs (
                    run_id, user_id, agent_instance_id, status, classification,
                    pending_action, subject, author, email_id, gmail_thread_id,
                    category, category_display_name, priority, template, workflow_owner,
                    workflow_approver, workflow_route_to, workflow_dept, assignee,
                    created_at, decision, decision_at, error, updated_at
                ) VALUES (
                    %(run_id)s, %(user_id)s, %(agent_instance_id)s, %(status)s,
                    %(classification)s, %(pending_action)s, %(subject)s, %(author)s,
                    %(email_id)s, %(gmail_thread_id)s, %(category)s,
                    %(category_display_name)s, %(priority)s, %(template)s, %(workflow_owner)s,
                    %(workflow_approver)s, %(workflow_route_to)s, %(workflow_dept)s, %(assignee)s,
                    %(created_at)s, %(decision)s, %(decision_at)s, %(error)s, %(updated_at)s
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
                    category = COALESCE(EXCLUDED.category, agent_runs.category),
                    category_display_name = COALESCE(EXCLUDED.category_display_name, agent_runs.category_display_name),
                    priority = COALESCE(EXCLUDED.priority, agent_runs.priority),
                    template = COALESCE(EXCLUDED.template, agent_runs.template),
                    workflow_owner = COALESCE(EXCLUDED.workflow_owner, agent_runs.workflow_owner),
                    workflow_approver = COALESCE(EXCLUDED.workflow_approver, agent_runs.workflow_approver),
                    workflow_route_to = COALESCE(EXCLUDED.workflow_route_to, agent_runs.workflow_route_to),
                    workflow_dept = COALESCE(EXCLUDED.workflow_dept, agent_runs.workflow_dept),
                    assignee = COALESCE(EXCLUDED.assignee, agent_runs.assignee),
                    created_at = COALESCE(agent_runs.created_at, EXCLUDED.created_at),
                    decision = COALESCE(EXCLUDED.decision, agent_runs.decision),
                    decision_at = COALESCE(EXCLUDED.decision_at, agent_runs.decision_at),
                    error = EXCLUDED.error,
                    updated_at = EXCLUDED.updated_at
                RETURNING run_id, user_id, agent_instance_id, status, classification,
                    pending_action, subject, author, email_id, gmail_thread_id,
                    category, category_display_name, priority, template, workflow_owner,
                    workflow_approver, workflow_route_to, workflow_dept, assignee,
                    created_at, decision, decision_at, error, updated_at
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
                    pending_action, subject, author, email_id, gmail_thread_id,
                    category, category_display_name, priority, template, workflow_owner,
                    workflow_approver, workflow_route_to, workflow_dept, assignee,
                    created_at, decision, decision_at, error, updated_at
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
                    pending_action, subject, author, email_id, gmail_thread_id,
                    category, category_display_name, priority, template, workflow_owner,
                    workflow_approver, workflow_route_to, workflow_dept, assignee,
                    created_at, decision, decision_at, error, updated_at
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
    created_at: str | None = None,
    decision: str | None = None,
    decision_at: str | None = None,
) -> dict:
    record = _record(
        run_id,
        status,
        email_input,
        classification,
        pending_action,
        user_id,
        agent_instance_id,
        created_at,
        decision,
        decision_at,
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


def claim_run(
    run_id: str,
    expected_status: str,
    new_status: str,
    path: str | Path | None = None,
    agent_instance_id: str | None = None,
) -> dict | None:
    """Atomically move a run between statuses and return the claimed record."""
    instance_id = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    if selected_run_registry_backend(path) == "postgres":
        from psycopg.rows import dict_row

        setup_run_registry()
        with _connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE agent_runs
                    SET status = %(new_status)s, updated_at = NOW()
                    WHERE run_id = %(run_id)s
                      AND agent_instance_id = %(agent_instance_id)s
                      AND status = %(expected_status)s
                    RETURNING run_id, user_id, agent_instance_id, status,
                        classification, pending_action, subject, author, email_id,
                        gmail_thread_id, category, category_display_name, priority,
                        template, workflow_owner, workflow_approver,
                        workflow_route_to, workflow_dept, assignee,
                        created_at, decision, decision_at, error, updated_at
                    """,
                    {
                        "run_id": run_id,
                        "agent_instance_id": instance_id,
                        "expected_status": expected_status,
                        "new_status": new_status,
                    },
                )
                row = cur.fetchone()
                return _postgres_row(row) if row else None

    with _json_transition_lock:
        index_path = _path(path)
        data = _read(index_path)
        record = next(
            (
                item
                for item in data.get("runs", [])
                if item.get("run_id") == run_id
                and normalize_agent_instance_id(item.get("agent_instance_id")) == instance_id
                and item.get("status") == expected_status
            ),
            None,
        )
        if record is None:
            return None
        record["status"] = new_status
        record["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _write(index_path, data)
        return record.copy()


def assign_run(
    run_id: str,
    assignee: str | None,
    path: str | Path | None = None,
    agent_instance_id: str | None = None,
) -> dict | None:
    instance_id = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    if selected_run_registry_backend(path) == "postgres":
        from psycopg.rows import dict_row

        setup_run_registry()
        with _connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    UPDATE agent_runs
                    SET assignee = %(assignee)s, updated_at = NOW()
                    WHERE run_id = %(run_id)s
                      AND agent_instance_id = %(agent_instance_id)s
                    RETURNING run_id, user_id, agent_instance_id, status,
                        classification, pending_action, subject, author, email_id,
                        gmail_thread_id, category, category_display_name, priority,
                        template, workflow_owner, workflow_approver,
                        workflow_route_to, workflow_dept, assignee,
                        created_at, decision, decision_at, error, updated_at
                    """,
                    {
                        "run_id": run_id,
                        "agent_instance_id": instance_id,
                        "assignee": assignee,
                    },
                )
                row = cur.fetchone()
                return _postgres_row(row) if row else None

    with _json_transition_lock:
        index_path = _path(path)
        data = _read(index_path)
        record = next(
            (
                item
                for item in data.get("runs", [])
                if item.get("run_id") == run_id
                and normalize_agent_instance_id(item.get("agent_instance_id")) == instance_id
            ),
            None,
        )
        if record is None:
            return None
        record["assignee"] = assignee
        record["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _write(index_path, data)
        return record.copy()





def list_runs_before(
    cutoff: datetime,
    path: str | Path | None = None,
    agent_instance_id: str | None = None,
) -> list[dict]:
    cutoff = cutoff.astimezone(timezone.utc)
    if selected_run_registry_backend(path) == "postgres":
        from psycopg.rows import dict_row

        setup_run_registry()
        params: dict[str, Any] = {"cutoff": cutoff}
        clauses = ["COALESCE(created_at, updated_at) < %(cutoff)s"]
        if agent_instance_id is not None:
            clauses.append("agent_instance_id = %(agent_instance_id)s")
            params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
        with _connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT run_id, user_id, agent_instance_id, status, classification,
                        pending_action, subject, author, email_id, gmail_thread_id,
                        category, category_display_name, priority, template, workflow_owner,
                        workflow_approver, workflow_route_to, workflow_dept, assignee,
                        created_at, decision, decision_at, error, updated_at
                    FROM agent_runs
                    WHERE
                    """
                    + " AND ".join(clauses)
                    + " ORDER BY COALESCE(created_at, updated_at) ASC",
                    params,
                )
                return [_postgres_row(row) for row in cur.fetchall()]
    records = _json_list(
        status=None,
        path=path,
        user_id=None,
        agent_instance_id=agent_instance_id,
        limit=None,
        offset=0,
    )
    selected: list[dict] = []
    for record in records:
        stamp = _parse_timestamp(record.get("created_at") or record.get("updated_at"))
        if stamp is not None and stamp < cutoff:
            selected.append(record)
    selected.sort(key=lambda item: item.get("created_at") or item.get("updated_at") or "")
    return selected



def delete_runs(
    run_ids: list[str],
    path: str | Path | None = None,
    agent_instance_id: str | None = None,
) -> int:
    run_ids = [str(run_id) for run_id in run_ids if run_id]
    if not run_ids:
        return 0
    if selected_run_registry_backend(path) == "postgres":
        setup_run_registry()
        clauses = ["run_id = ANY(%(run_ids)s)"]
        params: dict[str, Any] = {"run_ids": run_ids}
        if agent_instance_id is not None:
            clauses.append("agent_instance_id = %(agent_instance_id)s")
            params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM agent_runs WHERE " + " AND ".join(clauses),
                    params,
                )
                return cur.rowcount or 0
    index_path = _path(path)
    data = _read(index_path)
    runs = data.get("runs", [])
    kept = []
    deleted = 0
    for record in runs:
        matches_run = record.get("run_id") in run_ids
        matches_instance = (
            agent_instance_id is None
            or normalize_agent_instance_id(record.get("agent_instance_id")) == normalize_agent_instance_id(agent_instance_id)
        )
        if matches_run and matches_instance:
            deleted += 1
        else:
            kept.append(record)
    data["runs"] = kept
    _write(index_path, data)
    return deleted


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
