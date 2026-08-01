from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.config import SERVICE_ROOT, settings
from src.postgres import tenant_connection
from src.run_registry import selected_run_registry_backend
from src.tenant import (
    current_agent_instance_id,
    normalize_agent_instance_id,
    normalize_user_id,
    user_context,
)

DEFAULT_SYNC_STATUS_PATH = SERVICE_ROOT / "logs" / "gmail_sync_status.json"

_AUTH_ERROR_MARKERS = ("invalid_grant", "token has been expired", "credentials", "unauthorized", "401")


def _path() -> Path:
    p = Path(settings.gmail_sync_status_path)
    return p if p.is_absolute() else SERVICE_ROOT / p


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_auth_error(error: str) -> bool:
    low = error.lower()
    return any(m in low for m in _AUTH_ERROR_MARKERS)


def _connect():
    return tenant_connection()


def setup_sync_status() -> None:
    if selected_run_registry_backend() != "postgres":
        return
    # Postgres schema is owned by Alembic migrations. JSON dev path stays unchanged.
    return


# --- JSON backend ---

def _json_read() -> dict:
    path = _path()
    if not path.is_file():
        return {"statuses": {}}
    return json.loads(path.read_text() or "{}") or {"statuses": {}}


def _json_get(user_id: str, instance_id: str) -> dict:
    return _json_read().get("statuses", {}).get(user_id, {}).get(instance_id, {})


def _json_update(user_id: str, instance_id: str, patch: dict) -> None:
    data = _json_read()
    statuses = data.setdefault("statuses", {})
    by_user = statuses.setdefault(user_id, {})
    existing = by_user.get(instance_id, {})
    existing.update(patch)
    existing["user_id"] = user_id
    existing["agent_instance_id"] = instance_id
    existing.setdefault("provider", "gmail")
    existing.setdefault("connection_status", "disconnected")
    existing.setdefault("sync_mode", "idle")
    existing.setdefault("paused", False)
    by_user[instance_id] = existing
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


# --- Postgres backend ---

def _pg_get(user_id: str, instance_id: str) -> dict:
    setup_sync_status()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT provider, connection_status, sync_mode,
                       last_success_at, last_failure_at, last_error,
                       watch_expires_at, paused
                FROM email_agent_sync
                WHERE user_id = %s AND agent_instance_id = %s
                """,
                (user_id, instance_id),
            )
            row = cur.fetchone()
            if not row:
                return {}
            cols = ["provider", "connection_status", "sync_mode",
                    "last_success_at", "last_failure_at", "last_error",
                    "watch_expires_at", "paused"]
            result = dict(zip(cols, row))
            # Convert timestamps to ISO strings for a uniform API response.
            for k in ("last_success_at", "last_failure_at", "watch_expires_at"):
                if result.get(k) is not None:
                    result[k] = result[k].isoformat(timespec="seconds")
            result["user_id"] = user_id
            result["agent_instance_id"] = instance_id
            return result


def _pg_update(user_id: str, instance_id: str, patch: dict) -> None:
    setup_sync_status()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO email_agent_sync
                    (user_id, agent_instance_id, provider, connection_status, sync_mode,
                     last_success_at, last_failure_at, last_error, watch_expires_at, paused, updated_at)
                VALUES
                    (%(user_id)s, %(agent_instance_id)s, %(provider)s,
                     COALESCE(%(connection_status)s, 'disconnected'),
                     COALESCE(%(sync_mode)s, 'idle'),
                     %(last_success_at)s, %(last_failure_at)s, %(last_error)s, %(watch_expires_at)s,
                     COALESCE(%(paused)s, FALSE), NOW())
                ON CONFLICT (user_id, agent_instance_id) DO UPDATE SET
                    connection_status = COALESCE(%(connection_status)s, email_agent_sync.connection_status),
                    sync_mode         = COALESCE(%(sync_mode)s, email_agent_sync.sync_mode),
                    last_success_at   = COALESCE(%(last_success_at)s, email_agent_sync.last_success_at),
                    last_failure_at   = COALESCE(%(last_failure_at)s, email_agent_sync.last_failure_at),
                    last_error        = COALESCE(%(last_error)s, email_agent_sync.last_error),
                    watch_expires_at  = COALESCE(%(watch_expires_at)s, email_agent_sync.watch_expires_at),
                    paused            = COALESCE(%(paused)s, email_agent_sync.paused),
                    updated_at        = NOW()
                """,
                {
                    "user_id": user_id,
                    "agent_instance_id": instance_id,
                    "provider": patch.get("provider", "gmail"),
                    "connection_status": patch.get("connection_status"),
                    "sync_mode": patch.get("sync_mode"),
                    "last_success_at": patch.get("last_success_at"),
                    "last_failure_at": patch.get("last_failure_at"),
                    "last_error": patch.get("last_error"),
                    "watch_expires_at": patch.get("watch_expires_at"),
                    "paused": patch.get("paused"),
                },
            )


# --- Public API ---

def _resolve(user_id: str | None, instance_id: str | None) -> tuple[str, str]:
    """Connection status describes the mailbox, so it is stored per instance.

    Keying it on the acting user split one connection into a private record per
    delegate: an approver opening the workspace saw "disconnected" for a mailbox
    the owner had connected, and each of them drove their own reconnect. The
    identity is a constant for the same reason the Gmail baseline is one.
    """
    return (
        normalize_user_id(settings.default_user_id),
        normalize_agent_instance_id(instance_id or current_agent_instance_id()),
    )


def get_status(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> dict:
    uid, iid = _resolve(user_id, agent_instance_id)
    if selected_run_registry_backend() == "postgres":
        with user_context(uid):
            raw = _pg_get(uid, iid)
    else:
        raw = _json_get(uid, iid)
    return {
        "user_id": uid,
        "agent_instance_id": iid,
        "provider": raw.get("provider", "gmail"),
        "connection_status": raw.get("connection_status", "disconnected"),
        "sync_mode": raw.get("sync_mode", "idle"),
        "last_success_at": raw.get("last_success_at"),
        "last_failure_at": raw.get("last_failure_at"),
        "last_error": raw.get("last_error"),
        "watch_expires_at": raw.get("watch_expires_at"),
        "paused": bool(raw.get("paused", False)),
    }


def latest_success_at() -> str | None:
    """Most recent successful sync across ALL users/instances.

    The global health page has no instance context; without this it would show
    the default instance's timestamp, which can be stale while another instance
    is actively syncing."""
    if selected_run_registry_backend() == "postgres":
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(last_success_at) FROM email_agent_sync")
                row = cur.fetchone()
                value = row[0] if row else None
                if value is None:
                    return None
                # The column may be timestamptz (datetime) or text depending on
                # migration age; always hand back an ISO string like get_status.
                return value if isinstance(value, str) else value.isoformat()
    statuses = _json_read().get("statuses", {})
    values = [
        entry.get("last_success_at")
        for per_user in statuses.values()
        if isinstance(per_user, dict)
        for entry in per_user.values()
        if isinstance(entry, dict) and entry.get("last_success_at")
    ]
    return max(values) if values else None


def record_success(
    mode: str,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
    watch_expires_at: str | None = None,
) -> None:
    uid, iid = _resolve(user_id, agent_instance_id)
    patch: dict = {
        "connection_status": "connected",
        "sync_mode": mode,
        "last_success_at": _now(),
        "last_error": None,
    }
    if watch_expires_at is not None:
        patch["watch_expires_at"] = watch_expires_at
    if selected_run_registry_backend() == "postgres":
        with user_context(uid):
            _pg_update(uid, iid, patch)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE email_agent_sync
                    SET last_error = NULL, last_failure_at = NULL, updated_at = NOW()
                    WHERE user_id = %s AND agent_instance_id = %s
                    """,
                    (uid, iid),
                )
    else:
        _json_update(uid, iid, patch)




def public_error_message(error: str) -> str:
    raw = str(error or "")
    lowered = raw.lower()
    # Gmail's own 429 also says "rate limit", so check for it before blaming
    # the AI provider.
    if "gmail.googleapis.com" in lowered or "user-rate limit" in lowered or "ratelimitexceeded" in lowered:
        return "Gmail rate limit reached. Sync is paused; it resumes automatically once Google lifts the limit."
    if "rate_limit" in lowered or "rate limit" in lowered or "429" in lowered:
        return "AI provider rate limit reached. Wait a few minutes and try again."
    if "invalid_grant" in lowered or "expired or revoked" in lowered or "token has been expired" in lowered:
        return "Gmail authorization expired or was revoked. Reconnect Gmail."
    if "could not locate runnable browser" in lowered or "oauth" in lowered or "credentials" in lowered:
        return "Gmail sync is unavailable. Check the Gmail connection settings."
    return "Gmail sync failed. Check service logs for details."


def record_failure(
    error: str,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> None:
    uid, iid = _resolve(user_id, agent_instance_id)
    status = "expired" if _is_auth_error(error) else "error"
    patch = {
        "connection_status": status,
        "last_failure_at": _now(),
        "last_error": public_error_message(error),
    }
    if selected_run_registry_backend() == "postgres":
        with user_context(uid):
            _pg_update(uid, iid, patch)
    else:
        _json_update(uid, iid, patch)


def set_paused(
    paused: bool,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> None:
    uid, iid = _resolve(user_id, agent_instance_id)
    patch = {"paused": paused}
    if selected_run_registry_backend() == "postgres":
        with user_context(uid):
            _pg_update(uid, iid, patch)
    else:
        _json_update(uid, iid, patch)
