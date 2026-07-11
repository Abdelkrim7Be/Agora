from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from src.config import SERVICE_ROOT, settings
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
)

DEFAULT_DLQ_PATH = SERVICE_ROOT / "logs" / "dlq.json"
_REDIS_PREFIX = "agora:email-agent:dlq"


def _path(path: str | Path | None = None) -> Path:
    if path is None:
        path = settings.dlq_path or DEFAULT_DLQ_PATH
    p = Path(path)
    return p if p.is_absolute() else SERVICE_ROOT / p


class DlqConflictError(RuntimeError):
    pass


def selected_dlq_backend(path: str | Path | None = None) -> str:
    if path is not None:
        return "json"
    backend = settings.dlq_backend.lower().strip()
    if backend not in {"json", "postgres", "redis"}:
        raise RuntimeError(f"Unsupported AGENT_DLQ_BACKEND: {settings.dlq_backend}")
    if backend == "postgres" and not settings.database_url:
        raise RuntimeError("DATABASE_URL is required when AGENT_DLQ_BACKEND=postgres")
    if backend == "redis" and not settings.redis_url:
        raise RuntimeError("REDIS_URL is required when AGENT_DLQ_BACKEND=redis")
    return backend


def _connect_postgres():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Postgres DLQ requires psycopg.") from exc
    return psycopg.connect(settings.database_url)


def _redis_client():
    try:
        from redis import Redis
    except ImportError as exc:
        raise RuntimeError("Redis DLQ requires the redis package.") from exc
    return Redis.from_url(settings.redis_url, decode_responses=True)


def setup_dlq() -> None:
    if selected_dlq_backend() != "postgres":
        return
    return


def _normalize_entry(entry: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = entry.get("payload") or {}
    return {
        "entry_id": str(entry.get("entry_id") or uuid.uuid4()),
        "timestamp": str(entry.get("timestamp") or now),
        "user_id": normalize_user_id(entry.get("user_id") or current_user_id()),
        "agent_instance_id": normalize_agent_instance_id(
            entry.get("agent_instance_id") or current_agent_instance_id()
        ),
        "run_id": str(entry.get("run_id") or ""),
        "message_id": str(entry.get("message_id") or payload.get("email_id") or ""),
        "gmail_thread_id": str(entry.get("gmail_thread_id") or payload.get("gmail_thread_id") or ""),
        "reason": str(entry.get("reason") or "unknown"),
        "error": str(entry.get("error") or ""),
        "status": str(entry.get("status") or "dead_letter"),
        "requeue_token": str(entry.get("requeue_token") or ""),
        "requeued_at": str(entry.get("requeued_at") or ""),
        "resolved_at": str(entry.get("resolved_at") or ""),
        "payload": payload,
    }


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {"entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _json_upsert(entry: dict, path: str | Path | None = None) -> dict:
    target = _path(path)
    data = _read_json(target)
    entries = data.setdefault("entries", [])
    existing = next((item for item in entries if item.get("entry_id") == entry["entry_id"]), None)
    if existing is None:
        entries.append(entry)
    else:
        existing.update(entry)
        entry = existing
    entries.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    _write_json(target, data)
    return entry


def _json_list(status: str | None = None, limit: int = 100, path: str | Path | None = None, agent_instance_id: str | None = None) -> list[dict]:
    entries = _read_json(_path(path)).get("entries", [])
    if agent_instance_id is not None:
        resolved = normalize_agent_instance_id(agent_instance_id)
        entries = [item for item in entries if normalize_agent_instance_id(item.get("agent_instance_id")) == resolved]
    if status is not None:
        entries = [item for item in entries if item.get("status") == status]
    entries.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return entries[:limit]


def _json_get(entry_id: str, path: str | Path | None = None, agent_instance_id: str | None = None) -> dict | None:
    for item in _json_list(status=None, limit=5000, path=path, agent_instance_id=agent_instance_id):
        if item.get("entry_id") == entry_id:
            return item
    return None


def _json_claim(entry_id: str, expected_status: str, new_status: str, path: str | Path | None = None, agent_instance_id: str | None = None, requeue_token: str | None = None) -> dict | None:
    target = _path(path)
    data = _read_json(target)
    resolved = normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())
    for item in data.get("entries", []):
        if item.get("entry_id") != entry_id:
            continue
        if normalize_agent_instance_id(item.get("agent_instance_id")) != resolved:
            continue
        if item.get("status") != expected_status:
            return None
        item["status"] = new_status
        if new_status == "requeued":
            item["requeue_token"] = requeue_token or item.get("requeue_token", "")
            item["requeued_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            item["resolved_at"] = ""
        elif new_status == "resolved":
            item["resolved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _write_json(target, data)
        return item.copy()
    return None


def _json_count(status: str | None = None, path: str | Path | None = None, agent_instance_id: str | None = None) -> int:
    return len(_json_list(status=status, limit=5000, path=path, agent_instance_id=agent_instance_id))


def _pg_row(row: dict[str, Any]) -> dict:
    normalized = dict(row)
    for key in ("timestamp", "requeued_at", "resolved_at"):
        value = normalized.get(key)
        if hasattr(value, "isoformat"):
            normalized[key] = value.isoformat(timespec="seconds")
    payload = normalized.get("payload")
    if isinstance(payload, str):
        normalized["payload"] = json.loads(payload)
    return normalized


def _pg_upsert(entry: dict) -> dict:
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    with _connect_postgres() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO email_agent_dlq (
                    entry_id, timestamp, user_id, agent_instance_id, run_id, message_id,
                    gmail_thread_id, reason, error, status, requeue_token, requeued_at,
                    resolved_at, payload
                ) VALUES (
                    %(entry_id)s, %(timestamp)s, %(user_id)s, %(agent_instance_id)s,
                    %(run_id)s, %(message_id)s, %(gmail_thread_id)s, %(reason)s,
                    %(error)s, %(status)s, %(requeue_token)s, %(requeued_at)s,
                    %(resolved_at)s, %(payload)s
                )
                ON CONFLICT (entry_id) DO UPDATE SET
                    error = EXCLUDED.error,
                    status = EXCLUDED.status,
                    requeue_token = EXCLUDED.requeue_token,
                    requeued_at = EXCLUDED.requeued_at,
                    resolved_at = EXCLUDED.resolved_at,
                    payload = EXCLUDED.payload
                RETURNING entry_id, timestamp, user_id, agent_instance_id, run_id, message_id,
                    gmail_thread_id, reason, error, status, requeue_token, requeued_at,
                    resolved_at, payload
                """,
                {**entry, "payload": Jsonb(entry["payload"])},
            )
            return _pg_row(cur.fetchone())


def _pg_list(status: str | None = None, limit: int = 100, agent_instance_id: str | None = None) -> list[dict]:
    from psycopg.rows import dict_row

    clauses = []
    params: dict[str, Any] = {"limit": max(1, min(limit, 5000))}
    if status is not None:
        clauses.append("status = %(status)s")
        params["status"] = status
    if agent_instance_id is not None:
        clauses.append("agent_instance_id = %(agent_instance_id)s")
        params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect_postgres() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT entry_id, timestamp, user_id, agent_instance_id, run_id, message_id, gmail_thread_id, reason, error, status, requeue_token, requeued_at, resolved_at, payload FROM email_agent_dlq"
                + where +
                " ORDER BY timestamp DESC LIMIT %(limit)s",
                params,
            )
            return [_pg_row(row) for row in cur.fetchall()]


def _pg_get(entry_id: str, agent_instance_id: str | None = None) -> dict | None:
    from psycopg.rows import dict_row

    clauses = ["entry_id = %(entry_id)s"]
    params: dict[str, Any] = {"entry_id": entry_id}
    if agent_instance_id is not None:
        clauses.append("agent_instance_id = %(agent_instance_id)s")
        params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
    with _connect_postgres() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT entry_id, timestamp, user_id, agent_instance_id, run_id, message_id, gmail_thread_id, reason, error, status, requeue_token, requeued_at, resolved_at, payload FROM email_agent_dlq WHERE " + " AND ".join(clauses),
                params,
            )
            row = cur.fetchone()
            return _pg_row(row) if row else None


def _pg_claim(entry_id: str, expected_status: str, new_status: str, agent_instance_id: str | None = None, requeue_token: str | None = None) -> dict | None:
    from psycopg.rows import dict_row

    params: dict[str, Any] = {
        "entry_id": entry_id,
        "expected_status": expected_status,
        "new_status": new_status,
        "requeue_token": requeue_token or "",
        "requeued_at": datetime.now(timezone.utc),
    }
    clauses = ["entry_id = %(entry_id)s", "status = %(expected_status)s"]
    if agent_instance_id is not None:
        clauses.append("agent_instance_id = %(agent_instance_id)s")
        params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
    with _connect_postgres() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE email_agent_dlq
                SET status = %(new_status)s,
                    requeue_token = %(requeue_token)s,
                    requeued_at = CASE WHEN %(new_status)s = 'requeued' THEN %(requeued_at)s ELSE requeued_at END,
                    resolved_at = CASE WHEN %(new_status)s = 'resolved' THEN %(requeued_at)s WHEN %(new_status)s = 'requeued' THEN NULL ELSE resolved_at END
                WHERE
                """
                + " AND ".join(clauses)
                + " RETURNING entry_id, timestamp, user_id, agent_instance_id, run_id, message_id, gmail_thread_id, reason, error, status, requeue_token, requeued_at, resolved_at, payload",
                params,
            )
            row = cur.fetchone()
            return _pg_row(row) if row else None


def _pg_count(status: str | None = None, agent_instance_id: str | None = None) -> int:
    clauses = []
    params: dict[str, Any] = {}
    if status is not None:
        clauses.append("status = %(status)s")
        params["status"] = status
    if agent_instance_id is not None:
        clauses.append("agent_instance_id = %(agent_instance_id)s")
        params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect_postgres() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM email_agent_dlq" + where, params)
            row = cur.fetchone()
            return int(row[0] if row else 0)


def _redis_entry_key(entry_id: str) -> str:
    return f"{_REDIS_PREFIX}:entry:{entry_id}"


def _redis_index_key() -> str:
    return f"{_REDIS_PREFIX}:index"


def _redis_upsert(entry: dict) -> dict:
    client = _redis_client()
    key = _redis_entry_key(entry["entry_id"])
    payload = {field: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value) for field, value in entry.items()}
    client.hset(key, mapping=payload)
    timestamp = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
    client.zadd(_redis_index_key(), {entry["entry_id"]: timestamp})
    return entry


def _redis_decode(values: dict[str, str]) -> dict:
    decoded: dict[str, Any] = {}
    for key, value in values.items():
        if key == "payload":
            decoded[key] = json.loads(value)
        else:
            decoded[key] = value
    return decoded


def _redis_get(entry_id: str, agent_instance_id: str | None = None) -> dict | None:
    client = _redis_client()
    data = client.hgetall(_redis_entry_key(entry_id))
    if not data:
        return None
    decoded = _redis_decode(data)
    if agent_instance_id is not None and normalize_agent_instance_id(decoded.get("agent_instance_id")) != normalize_agent_instance_id(agent_instance_id):
        return None
    return decoded


def _redis_list(status: str | None = None, limit: int = 100, agent_instance_id: str | None = None) -> list[dict]:
    client = _redis_client()
    ids = client.zrevrange(_redis_index_key(), 0, max(0, limit - 1))
    rows = []
    for entry_id in ids:
        row = _redis_get(entry_id, agent_instance_id=agent_instance_id)
        if not row:
            continue
        if status is not None and row.get("status") != status:
            continue
        rows.append(row)
    return rows


def _redis_claim(entry_id: str, expected_status: str, new_status: str, agent_instance_id: str | None = None, requeue_token: str | None = None) -> dict | None:
    client = _redis_client()
    row = _redis_get(entry_id, agent_instance_id=agent_instance_id)
    if not row or row.get("status") != expected_status:
        return None
    updates = {"status": new_status}
    if new_status == "requeued":
        updates["requeue_token"] = requeue_token or row.get("requeue_token", "")
        updates["requeued_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        updates["resolved_at"] = ""
    elif new_status == "resolved":
        updates["resolved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    client.hset(_redis_entry_key(entry_id), mapping=updates)
    return _redis_get(entry_id, agent_instance_id=agent_instance_id)


def _redis_count(status: str | None = None, agent_instance_id: str | None = None) -> int:
    return len(_redis_list(status=status, limit=5000, agent_instance_id=agent_instance_id))


def record_dead_letter(entry: dict, path: str | Path | None = None) -> dict:
    normalized = _normalize_entry(entry)
    backend = selected_dlq_backend(path)
    if backend == "postgres":
        return _pg_upsert(normalized)
    if backend == "redis":
        return _redis_upsert(normalized)
    return _json_upsert(normalized, path=path)


def list_dead_letters(status: str | None = None, limit: int = 100, path: str | Path | None = None, agent_instance_id: str | None = None) -> list[dict]:
    backend = selected_dlq_backend(path)
    if backend == "postgres":
        return _pg_list(status=status, limit=limit, agent_instance_id=agent_instance_id)
    if backend == "redis":
        return _redis_list(status=status, limit=limit, agent_instance_id=agent_instance_id)
    return _json_list(status=status, limit=limit, path=path, agent_instance_id=agent_instance_id)


def get_dead_letter(entry_id: str, path: str | Path | None = None, agent_instance_id: str | None = None) -> dict | None:
    backend = selected_dlq_backend(path)
    if backend == "postgres":
        return _pg_get(entry_id, agent_instance_id=agent_instance_id)
    if backend == "redis":
        return _redis_get(entry_id, agent_instance_id=agent_instance_id)
    return _json_get(entry_id, path=path, agent_instance_id=agent_instance_id)


def claim_dead_letter(entry_id: str, expected_status: str, new_status: str, path: str | Path | None = None, agent_instance_id: str | None = None, requeue_token: str | None = None) -> dict | None:
    backend = selected_dlq_backend(path)
    if backend == "postgres":
        return _pg_claim(entry_id, expected_status, new_status, agent_instance_id=agent_instance_id, requeue_token=requeue_token)
    if backend == "redis":
        return _redis_claim(entry_id, expected_status, new_status, agent_instance_id=agent_instance_id, requeue_token=requeue_token)
    return _json_claim(entry_id, expected_status, new_status, path=path, agent_instance_id=agent_instance_id, requeue_token=requeue_token)


def count_dead_letters(status: str | None = None, path: str | Path | None = None, agent_instance_id: str | None = None) -> int:
    backend = selected_dlq_backend(path)
    if backend == "postgres":
        return _pg_count(status=status, agent_instance_id=agent_instance_id)
    if backend == "redis":
        return _redis_count(status=status, agent_instance_id=agent_instance_id)
    return _json_count(status=status, path=path, agent_instance_id=agent_instance_id)
