from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from src.config import SERVICE_ROOT, settings
from src.postgres import tenant_connection
from src.run_registry import selected_run_registry_backend
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
)

DEFAULT_NOTIFICATION_STORE_PATH = SERVICE_ROOT / "logs" / "notifications.json"

SEVERITIES = ("info", "success", "warning", "error")

NOTIFICATION_TYPES = (
    "setup_completed",
    "setup_failed",
    "provider_auth_expired",
    "sync_failed",
    "drafts_pending",
    "draft_blocked",
    "approval_assigned",
    "approval_overdue",
    "campaign_ready",
    "campaign_completed",
    "bulk_send_completed",
    "security_warning",
)

_json_lock = Lock()
_next_id_lock = Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path() -> Path:
    p = Path(settings.notification_store_path)
    return p if p.is_absolute() else SERVICE_ROOT / p


def setup_notification_store() -> None:
    if selected_run_registry_backend() != "postgres":
        return
    return


def _resolve(user_id: str | None, instance_id: str | None) -> tuple[str, str]:
    return (
        normalize_user_id(user_id or current_user_id()),
        normalize_agent_instance_id(instance_id or current_agent_instance_id()),
    )


# --- JSON backend ---

def _json_read() -> dict:
    path = _path()
    if not path.is_file():
        return {"next_id": 1, "notifications": []}
    data = json.loads(path.read_text() or "{}") or {}
    data.setdefault("next_id", 1)
    data.setdefault("notifications", [])
    return data


def _json_write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _json_create(
    user_id: str,
    instance_id: str,
    notification_type: str,
    title: str,
    body: str,
    severity: str,
    action_url: str | None,
    dedupe_key: str | None,
) -> dict:
    with _json_lock:
        data = _json_read()
        if dedupe_key:
            for row in data["notifications"]:
                if (
                    row["user_id"] == user_id
                    and row["agent_instance_id"] == instance_id
                    and row.get("dedupe_key") == dedupe_key
                    and row.get("read_at") is None
                ):
                    row["title"] = title
                    row["body"] = body
                    row["severity"] = severity
                    row["action_url"] = action_url
                    row["occurrence_count"] = row.get("occurrence_count", 1) + 1
                    row["updated_at"] = _now()
                    _json_write(data)
                    return row
        row = {
            "id": data["next_id"],
            "user_id": user_id,
            "agent_instance_id": instance_id,
            "notification_type": notification_type,
            "severity": severity,
            "title": title,
            "body": body,
            "action_url": action_url,
            "dedupe_key": dedupe_key,
            "occurrence_count": 1,
            "read_at": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        data["next_id"] += 1
        data["notifications"].append(row)
        _json_write(data)
        return row


def _json_list(user_id: str, unread_only: bool, limit: int, instance_id: str | None) -> list[dict]:
    data = _json_read()
    rows = [r for r in data["notifications"] if r["user_id"] == user_id]
    if instance_id is not None:
        rows = [r for r in rows if r["agent_instance_id"] == instance_id]
    if unread_only:
        rows = [r for r in rows if r.get("read_at") is None]
    # id as a tiebreaker: two notifications created within the same second
    # (created_at has second precision) must still sort newest-first.
    rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
    return rows[:limit]


def _json_unread_count(user_id: str, instance_id: str | None) -> int:
    data = _json_read()
    rows = [r for r in data["notifications"] if r["user_id"] == user_id and r.get("read_at") is None]
    if instance_id is not None:
        rows = [r for r in rows if r["agent_instance_id"] == instance_id]
    return len(rows)


def _json_mark_read(notification_id: int) -> dict | None:
    with _json_lock:
        data = _json_read()
        for row in data["notifications"]:
            if row["id"] == notification_id:
                row["read_at"] = _now()
                row["updated_at"] = _now()
                _json_write(data)
                return row
    return None


def _json_mark_all_read(user_id: str, instance_id: str | None) -> int:
    with _json_lock:
        data = _json_read()
        n = 0
        for row in data["notifications"]:
            if row["user_id"] != user_id or row.get("read_at") is not None:
                continue
            if instance_id is not None and row["agent_instance_id"] != instance_id:
                continue
            row["read_at"] = _now()
            row["updated_at"] = _now()
            n += 1
        if n:
            _json_write(data)
        return n


def _json_delete(notification_id: int) -> None:
    with _json_lock:
        data = _json_read()
        data["notifications"] = [r for r in data["notifications"] if r["id"] != notification_id]
        _json_write(data)


def _json_prune(older_than_days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat(timespec="seconds")
    with _json_lock:
        data = _json_read()
        before = len(data["notifications"])
        data["notifications"] = [r for r in data["notifications"] if r["created_at"] >= cutoff]
        removed = before - len(data["notifications"])
        if removed:
            _json_write(data)
        return removed


def _json_erase_for_subject(user_id: str, agent_instance_id: str) -> int:
    with _json_lock:
        data = _json_read()
        before = len(data["notifications"])
        data["notifications"] = [
            r
            for r in data["notifications"]
            if not (r["user_id"] == user_id and r["agent_instance_id"] == agent_instance_id)
        ]
        removed = before - len(data["notifications"])
        if removed:
            _json_write(data)
        return removed


# --- Postgres backend ---
# Unlike instance_setup/job_queue, notifications never need a cross-tenant scan
# (create/list/count are always for one resolved user+instance; mark/delete-by-id
# run inside the caller's ambient tenant context) so every query here goes
# through tenant_connection() and stays RLS-enforced (migration 0012).

def _pg_row(row: dict) -> dict:
    row = dict(row)
    for key in ("read_at", "created_at", "updated_at"):
        if row.get(key) is not None and hasattr(row[key], "isoformat"):
            row[key] = row[key].isoformat(timespec="seconds")
    return row


def _pg_create(
    user_id: str,
    instance_id: str,
    notification_type: str,
    title: str,
    body: str,
    severity: str,
    action_url: str | None,
    dedupe_key: str | None,
) -> dict:
    from psycopg.rows import dict_row

    setup_notification_store()
    with tenant_connection(user_id=user_id, agent_instance_id=instance_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if dedupe_key:
                cur.execute(
                    """
                    UPDATE email_agent_notification
                    SET title = %(title)s, body = %(body)s, severity = %(severity)s,
                        action_url = %(action_url)s, occurrence_count = occurrence_count + 1,
                        updated_at = NOW()
                    WHERE user_id = %(user_id)s AND agent_instance_id = %(instance_id)s
                      AND dedupe_key = %(dedupe_key)s AND read_at IS NULL
                    RETURNING *
                    """,
                    {
                        "user_id": user_id, "instance_id": instance_id, "dedupe_key": dedupe_key,
                        "title": title, "body": body, "severity": severity, "action_url": action_url,
                    },
                )
                row = cur.fetchone()
                if row:
                    conn.commit()
                    return _pg_row(row)
            cur.execute(
                """
                INSERT INTO email_agent_notification
                    (user_id, agent_instance_id, notification_type, severity, title, body,
                     action_url, dedupe_key, occurrence_count, created_at, updated_at)
                VALUES (%(user_id)s, %(instance_id)s, %(notification_type)s, %(severity)s, %(title)s,
                        %(body)s, %(action_url)s, %(dedupe_key)s, 1, NOW(), NOW())
                RETURNING *
                """,
                {
                    "user_id": user_id, "instance_id": instance_id, "notification_type": notification_type,
                    "severity": severity, "title": title, "body": body, "action_url": action_url,
                    "dedupe_key": dedupe_key,
                },
            )
            row = cur.fetchone()
            conn.commit()
    return _pg_row(row)


def _pg_list(user_id: str, unread_only: bool, limit: int, instance_id: str | None) -> list[dict]:
    from psycopg.rows import dict_row

    clauses = ["user_id = %(user_id)s"]
    params = {"user_id": user_id, "limit": limit}
    if instance_id is not None:
        clauses.append("agent_instance_id = %(instance_id)s")
        params["instance_id"] = instance_id
    if unread_only:
        clauses.append("read_at IS NULL")
    where = " AND ".join(clauses)
    with tenant_connection(user_id=user_id, agent_instance_id=instance_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT * FROM email_agent_notification WHERE {where} "
                f"ORDER BY created_at DESC, id DESC LIMIT %(limit)s",
                params,
            )
            rows = cur.fetchall()
    return [_pg_row(r) for r in rows]


def _pg_unread_count(user_id: str, instance_id: str | None) -> int:
    from psycopg.rows import dict_row

    clauses = ["user_id = %(user_id)s", "read_at IS NULL"]
    params = {"user_id": user_id}
    if instance_id is not None:
        clauses.append("agent_instance_id = %(instance_id)s")
        params["instance_id"] = instance_id
    where = " AND ".join(clauses)
    with tenant_connection(user_id=user_id, agent_instance_id=instance_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM email_agent_notification WHERE {where}", params)
            return int(cur.fetchone()["n"])


def _pg_mark_read(notification_id: int) -> dict | None:
    from psycopg.rows import dict_row

    with tenant_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "UPDATE email_agent_notification SET read_at = NOW(), updated_at = NOW() "
                "WHERE id = %s RETURNING *",
                (notification_id,),
            )
            row = cur.fetchone()
            conn.commit()
    return _pg_row(row) if row else None


def _pg_mark_all_read(user_id: str, instance_id: str | None) -> int:
    clauses = ["user_id = %(user_id)s", "read_at IS NULL"]
    params = {"user_id": user_id}
    if instance_id is not None:
        clauses.append("agent_instance_id = %(instance_id)s")
        params["instance_id"] = instance_id
    where = " AND ".join(clauses)
    with tenant_connection(user_id=user_id, agent_instance_id=instance_id) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE email_agent_notification SET read_at = NOW(), updated_at = NOW() WHERE {where}",
                params,
            )
            n = cur.rowcount
            conn.commit()
    return n


def _pg_delete(notification_id: int) -> None:
    with tenant_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM email_agent_notification WHERE id = %s", (notification_id,))
            conn.commit()


def _pg_prune(older_than_days: int) -> int:
    # Retention runs per-instance (src.retention mirrors this for every RLS'd
    # table), so the ambient tenant context from the caller scopes this delete.
    with tenant_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM email_agent_notification WHERE created_at < NOW() - make_interval(days => %s)",
                (older_than_days,),
            )
            n = cur.rowcount
            conn.commit()
    return n


def _pg_erase_for_subject(user_id: str, agent_instance_id: str) -> int:
    with tenant_connection(user_id=user_id, agent_instance_id=agent_instance_id) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM email_agent_notification WHERE user_id = %s", (user_id,))
            n = cur.rowcount
            conn.commit()
    return n


# --- Public API ---

def create_notification(
    *,
    notification_type: str,
    title: str,
    body: str = "",
    severity: str = "info",
    action_url: str | None = None,
    dedupe_key: str | None = None,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> dict:
    uid, iid = _resolve(user_id, agent_instance_id)
    severity = severity if severity in SEVERITIES else "info"
    if selected_run_registry_backend() == "postgres":
        return _pg_create(uid, iid, notification_type, title, body, severity, action_url, dedupe_key)
    return _json_create(uid, iid, notification_type, title, body, severity, action_url, dedupe_key)


def list_notifications(
    *, unread_only: bool = False, limit: int = 50, agent_instance_id: str | None = None, user_id: str | None = None
) -> list[dict]:
    uid, iid = _resolve(user_id, agent_instance_id)
    limit = max(1, min(limit, 200))
    if selected_run_registry_backend() == "postgres":
        return _pg_list(uid, unread_only, limit, iid)
    return _json_list(uid, unread_only, limit, iid)


def unread_count(agent_instance_id: str | None = None, user_id: str | None = None) -> int:
    uid, iid = _resolve(user_id, agent_instance_id)
    if selected_run_registry_backend() == "postgres":
        return _pg_unread_count(uid, iid)
    return _json_unread_count(uid, iid)


def mark_read(notification_id: int) -> dict | None:
    if selected_run_registry_backend() == "postgres":
        return _pg_mark_read(notification_id)
    return _json_mark_read(notification_id)


def mark_all_read(agent_instance_id: str | None = None, user_id: str | None = None) -> int:
    uid, iid = _resolve(user_id, agent_instance_id)
    if selected_run_registry_backend() == "postgres":
        return _pg_mark_all_read(uid, iid)
    return _json_mark_all_read(uid, iid)


def delete_notification(notification_id: int) -> None:
    if selected_run_registry_backend() == "postgres":
        _pg_delete(notification_id)
    else:
        _json_delete(notification_id)


def prune_notifications(older_than_days: int) -> int:
    if selected_run_registry_backend() == "postgres":
        return _pg_prune(older_than_days)
    return _json_prune(older_than_days)


def erase_notifications_for_subject(user_id: str, agent_instance_id: str | None = None) -> int:
    """Called from src.gdpr's subject-erasure orchestration (Article 17).

    Scoped like erase_subject itself: one instance per call, only meaningful
    when the subject IS the instance owner (gdpr.erase_subject's
    revoke_owner_token case) since notifications are keyed by platform user_id,
    not by an arbitrary correspondent's email address."""
    uid = normalize_user_id(user_id)
    iid = normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())
    if selected_run_registry_backend() == "postgres":
        return _pg_erase_for_subject(uid, iid)
    return _json_erase_for_subject(uid, iid)
