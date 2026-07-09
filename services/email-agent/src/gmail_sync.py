from __future__ import annotations

import json
from pathlib import Path

from src.config import SERVICE_ROOT, settings
from src.run_registry import selected_run_registry_backend
from src.tenant import (
    current_agent_instance_id,
    normalize_agent_instance_id,
    normalize_user_id,
)

DEFAULT_SYNC_PATH = SERVICE_ROOT / "logs" / "gmail_sync.json"


def _path() -> Path:
    p = Path(settings.gmail_sync_path)
    return p if p.is_absolute() else SERVICE_ROOT / p


def _is_newer(candidate: str, existing: str | None) -> bool:
    """Gmail historyIds increase monotonically; never move a baseline backwards."""
    if existing is None:
        return True
    try:
        return int(candidate) > int(existing)
    except (TypeError, ValueError):
        return candidate != existing


def history_id_is_newer(candidate: str, existing: str | None) -> bool:
    """Public helper for webhook/poller idempotency decisions."""
    return _is_newer(candidate, existing)


def _connect():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Postgres gmail sync state requires psycopg.") from exc
    return psycopg.connect(settings.database_url)


def setup_gmail_sync() -> None:
    if selected_run_registry_backend() != "postgres":
        return
    # Postgres schema is owned by Alembic migrations. JSON dev path stays unchanged.
    return


def _json_read() -> dict:
    path = _path()
    if not path.is_file():
        return {"users": {}}
    return json.loads(path.read_text() or "{}") or {"users": {}}


def _json_get(user_id: str, agent_instance_id: str) -> str | None:
    value = _json_read().get("users", {}).get(user_id)
    if isinstance(value, dict):
        return value.get(agent_instance_id)
    if agent_instance_id == normalize_agent_instance_id(settings.default_agent_instance_id):
        return value
    return None


def _json_set(user_id: str, agent_instance_id: str, history_id: str) -> None:
    data = _json_read()
    users = data.setdefault("users", {})
    existing = users.get(user_id)
    if isinstance(existing, dict):
        current = existing.get(agent_instance_id)
    elif agent_instance_id == normalize_agent_instance_id(settings.default_agent_instance_id):
        current = existing
    else:
        current = None
    if _is_newer(history_id, current):
        if (
            agent_instance_id == normalize_agent_instance_id(settings.default_agent_instance_id)
            and not isinstance(existing, dict)
        ):
            users[user_id] = history_id
        else:
            if not isinstance(existing, dict):
                existing = {}
            existing[agent_instance_id] = history_id
            users[user_id] = existing
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _pg_get(user_id: str, agent_instance_id: str) -> str | None:
    setup_gmail_sync()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT history_id FROM gmail_sync_state "
                "WHERE user_id = %s AND agent_instance_id = %s",
                (user_id, agent_instance_id),
            )
            row = cur.fetchone()
            return row[0] if row else None


def _pg_set(user_id: str, agent_instance_id: str, history_id: str) -> None:
    setup_gmail_sync()
    with _connect() as conn:
        with conn.cursor() as cur:
            # Advance only — a stale/out-of-order push must not rewind the baseline.
            cur.execute(
                """
                INSERT INTO gmail_sync_state (user_id, agent_instance_id, history_id, updated_at)
                VALUES (%(user_id)s, %(agent_instance_id)s, %(history_id)s, NOW())
                ON CONFLICT (user_id, agent_instance_id) DO UPDATE SET
                    history_id = EXCLUDED.history_id,
                    updated_at = NOW()
                WHERE EXCLUDED.history_id::bigint > gmail_sync_state.history_id::bigint
                """,
                {
                    "user_id": user_id,
                    "agent_instance_id": agent_instance_id,
                    "history_id": history_id,
                },
            )


def get_last_history_id(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> str | None:
    resolved = normalize_user_id(settings.default_user_id)
    resolved_instance = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    if selected_run_registry_backend() == "postgres":
        return _pg_get(resolved, resolved_instance)
    return _json_get(resolved, resolved_instance)


def set_last_history_id(
    history_id: str,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> None:
    if not history_id:
        return
    resolved = normalize_user_id(settings.default_user_id)
    resolved_instance = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    if selected_run_registry_backend() == "postgres":
        _pg_set(resolved, resolved_instance, history_id)
        return
    _json_set(resolved, resolved_instance, history_id)
