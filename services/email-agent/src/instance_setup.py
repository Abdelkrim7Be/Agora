from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr
import logging
from pathlib import Path
from threading import RLock
from typing import Any, Awaitable, Callable

from src.config import SERVICE_ROOT, settings
from src.run_registry import selected_run_registry_backend
from src.runtime_settings import load_runtime_settings
from src.sync_status import public_error_message as _sync_public_error_message
from src.tenant import (
    agent_instance_context,
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
    user_context,
)

logger = logging.getLogger(__name__)

DEFAULT_INSTANCE_SETUP_PATH = SERVICE_ROOT / "logs" / "instance_setup.json"

SETUP_STEPS: tuple[str, ...] = (
    "verify_provider",
    "fetch_recent",
    "seed_categories",
    "import_contacts",
    "learn_style",
    "suggest_persona",
    "detect_signature",
    "triage_backlog",
    "finalize",
)
STEP_POSITION: dict[str, int] = {key: i for i, key in enumerate(SETUP_STEPS)}
FATAL_STEPS: frozenset[str] = frozenset({"verify_provider", "fetch_recent", "finalize"})
TERMINAL_STATUSES: frozenset[str] = frozenset({"ready", "failed"})

_json_lock = RLock()  # _json_save_record is called from other _json_lock-held sections; must be reentrant.


class SkipStep(Exception):
    """Raised by a step handler to mark its step 'skipped' instead of 'done'/'failed'."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class SetupContext:
    user_id: str
    agent_instance_id: str
    provider: Any = None
    recent_messages: list[dict] = field(default_factory=list)
    sent_samples: list[dict] = field(default_factory=list)
    store: Any = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path() -> Path:
    p = Path(settings.instance_setup_path)
    return p if p.is_absolute() else SERVICE_ROOT / p


def public_error_message(error: str) -> str:
    raw = str(error or "")
    lowered = raw.lower()
    if any(m in lowered for m in ("invalid_grant", "credentials", "unauthorized", "401", "oauth")):
        return _sync_public_error_message(raw)
    if "rate_limit" in lowered or "rate limit" in lowered or "429" in lowered:
        return "AI provider rate limit reached during setup. It will retry automatically."
    return "A setup step failed. Check service logs for details."


def setup_instance_setup() -> None:
    if selected_run_registry_backend() != "postgres":
        return
    # Postgres schema is owned by Alembic migrations. JSON dev path stays unchanged.
    return


def _resolve(user_id: str | None, instance_id: str | None) -> tuple[str, str]:
    return (
        normalize_user_id(user_id or current_user_id()),
        normalize_agent_instance_id(instance_id or current_agent_instance_id()),
    )


def _new_steps() -> list[dict]:
    return [
        {
            "step_key": key,
            "position": STEP_POSITION[key],
            "status": "pending",
            "attempts": 0,
            "claimed_by": None,
            "claimed_at": None,
            "error": None,
            "detail": None,
            "started_at": None,
            "finished_at": None,
        }
        for key in SETUP_STEPS
    ]


def _public(record: dict) -> dict:
    steps = sorted(record.get("steps", []), key=lambda s: s["position"])
    done = sum(1 for s in steps if s["status"] in ("done", "skipped"))
    total = len(steps) or len(SETUP_STEPS)
    return {
        "status": record.get("status", "not_started"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "error": record.get("error"),
        "steps": [
            {
                "step_key": s["step_key"],
                "status": s["status"],
                "started_at": s.get("started_at"),
                "finished_at": s.get("finished_at"),
                "error": s.get("error"),
                "detail": s.get("detail"),
            }
            for s in steps
        ],
        "progress": {
            "done": done,
            "total": total,
            "percent": round(done * 100 / total) if total else 0,
        },
    }


# --- JSON backend ---

def _json_read() -> dict:
    path = _path()
    if not path.is_file():
        return {"setups": {}}
    return json.loads(path.read_text() or "{}") or {"setups": {}}


def _json_write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _json_get_record(user_id: str, instance_id: str) -> dict | None:
    return _json_read().get("setups", {}).get(user_id, {}).get(instance_id)


def _json_save_record(user_id: str, instance_id: str, record: dict) -> None:
    with _json_lock:
        data = _json_read()
        setups = data.setdefault("setups", {})
        by_user = setups.setdefault(user_id, {})
        record["user_id"] = user_id
        record["agent_instance_id"] = instance_id
        record["updated_at"] = _now()
        by_user[instance_id] = record
        _json_write(data)


def _json_start(user_id: str, instance_id: str, force: bool) -> dict:
    with _json_lock:
        existing = _json_get_record(user_id, instance_id)
    if existing and not force:
        return existing
    if existing and force:
        for step in existing["steps"]:
            if step["status"] in ("failed", "pending"):
                step["status"] = "pending"
                step["error"] = None
                step["started_at"] = None
                step["finished_at"] = None
        existing["status"] = "created"
        existing["error"] = None
        existing["finished_at"] = None
        _json_save_record(user_id, instance_id, existing)
        return existing
    record = {
        "status": "created",
        "started_at": _now(),
        "finished_at": None,
        "error": None,
        "steps": _new_steps(),
    }
    _json_save_record(user_id, instance_id, record)
    return record


def _json_claim_next_step(worker_id: str) -> dict | None:
    with _json_lock:
        data = _json_read()
        for user_id, by_instance in data.get("setups", {}).items():
            for instance_id, record in by_instance.items():
                if record.get("status") in TERMINAL_STATUSES:
                    continue
                steps = sorted(record.get("steps", []), key=lambda s: s["position"])
                for i, step in enumerate(steps):
                    if step["status"] != "pending":
                        continue
                    predecessors = steps[:i]
                    if not all(p["status"] in ("done", "skipped", "failed") for p in predecessors):
                        break
                    step["status"] = "running"
                    step["claimed_by"] = worker_id
                    step["claimed_at"] = _now()
                    step["started_at"] = _now()
                    step["attempts"] = step.get("attempts", 0) + 1
                    if record["status"] == "created":
                        record["status"] = "running_setup"
                    _json_save_record(user_id, instance_id, record)
                    return {
                        "setup_id": f"{user_id}::{instance_id}",
                        "user_id": user_id,
                        "agent_instance_id": instance_id,
                        "step_key": step["step_key"],
                    }
        return None


def _json_split_setup_id(setup_id: str) -> tuple[str, str]:
    user_id, instance_id = setup_id.split("::", 1)
    return user_id, instance_id


def _json_update_step(setup_id: str, step_key: str, patch: dict, *, fatal_failure: bool = False) -> None:
    user_id, instance_id = _json_split_setup_id(setup_id)
    with _json_lock:
        record = _json_get_record(user_id, instance_id)
        if not record:
            return
        for step in record["steps"]:
            if step["step_key"] == step_key:
                step.update(patch)
                break
        if fatal_failure:
            record["status"] = "failed"
            record["finished_at"] = _now()
            record["error"] = patch.get("error")
        _json_save_record(user_id, instance_id, record)


def _json_requeue_stale(stale_seconds: float, max_attempts: int) -> int:
    cutoff = datetime.now(timezone.utc).timestamp() - stale_seconds
    requeued = 0
    with _json_lock:
        data = _json_read()
        for user_id, by_instance in data.get("setups", {}).items():
            for instance_id, record in by_instance.items():
                changed = False
                for step in record.get("steps", []):
                    if step["status"] != "running" or not step.get("claimed_at"):
                        continue
                    claimed_at = datetime.fromisoformat(step["claimed_at"]).timestamp()
                    if claimed_at >= cutoff:
                        continue
                    if step.get("attempts", 0) >= max_attempts:
                        step["status"] = "abandoned"
                    else:
                        step["status"] = "pending"
                    step["claimed_by"] = None
                    step["claimed_at"] = None
                    changed = True
                    requeued += 1
                if changed:
                    _json_save_record(user_id, instance_id, record)
    return requeued


def _json_mark_ready(setup_id: str) -> None:
    user_id, instance_id = _json_split_setup_id(setup_id)
    with _json_lock:
        record = _json_get_record(user_id, instance_id)
        if not record:
            return
        record["status"] = "ready"
        record["finished_at"] = _now()
        _json_save_record(user_id, instance_id, record)


# --- Postgres backend ---
# email_agent_instance_setup / email_agent_instance_setup_step are deliberately NOT
# row-level-secured (see migration 0011) — claim_next_step must scan pending steps
# across every tenant in one atomic query, same rationale as agent_poll_jobs
# (migration 0010). Per-instance reads/writes filter explicitly by
# (user_id, agent_instance_id) in SQL instead of relying on session RLS context.

def _pg_connect():
    import psycopg

    return psycopg.connect(settings.database_url)


def _pg_row_to_record(setup_row: dict, step_rows: list[dict]) -> dict:
    return {
        "id": setup_row["id"],
        "status": setup_row["status"],
        "started_at": setup_row["started_at"].isoformat() if setup_row["started_at"] else None,
        "finished_at": setup_row["finished_at"].isoformat() if setup_row["finished_at"] else None,
        "error": setup_row["error"],
        "steps": [
            {
                "step_key": r["step_key"],
                "position": r["position"],
                "status": r["status"],
                "attempts": r["attempts"],
                "error": r["error"],
                "detail": r["detail"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
            }
            for r in sorted(step_rows, key=lambda r: r["position"])
        ],
    }


def _pg_get_record(user_id: str, instance_id: str) -> dict | None:
    from psycopg.rows import dict_row

    setup_instance_setup()
    with _pg_connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM email_agent_instance_setup WHERE user_id = %s AND agent_instance_id = %s",
                (user_id, instance_id),
            )
            setup_row = cur.fetchone()
            if not setup_row:
                cur.execute(
                    """
                    SELECT * FROM email_agent_instance_setup
                    WHERE agent_instance_id = %s AND status = 'ready'
                    ORDER BY finished_at DESC NULLS LAST, updated_at DESC
                    LIMIT 1
                    """,
                    (instance_id,),
                )
                setup_row = cur.fetchone()
            if not setup_row:
                return None
            cur.execute(
                "SELECT * FROM email_agent_instance_setup_step WHERE setup_id = %s ORDER BY position",
                (setup_row["id"],),
            )
            step_rows = cur.fetchall()
    return _pg_row_to_record(setup_row, step_rows)


def _pg_start(user_id: str, instance_id: str, force: bool) -> dict:
    from psycopg.rows import dict_row

    setup_instance_setup()
    existing = _pg_get_record(user_id, instance_id)
    if existing and not force:
        return existing
    with _pg_connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if existing:
                cur.execute(
                    """
                    UPDATE email_agent_instance_setup
                    SET status = 'created', error = NULL, finished_at = NULL, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (existing["id"],),
                )
                cur.execute(
                    """
                    UPDATE email_agent_instance_setup_step
                    SET status = 'pending', error = NULL, started_at = NULL, finished_at = NULL, updated_at = NOW()
                    WHERE setup_id = %s AND status IN ('failed', 'pending')
                    """,
                    (existing["id"],),
                )
                conn.commit()
                return _pg_get_record(user_id, instance_id)
            cur.execute(
                """
                INSERT INTO email_agent_instance_setup
                    (user_id, agent_instance_id, status, started_at, created_at, updated_at)
                VALUES (%s, %s, 'created', NOW(), NOW(), NOW())
                RETURNING id
                """,
                (user_id, instance_id),
            )
            setup_id = cur.fetchone()["id"]
            for key in SETUP_STEPS:
                cur.execute(
                    """
                    INSERT INTO email_agent_instance_setup_step
                        (setup_id, agent_instance_id, step_key, position, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'pending', NOW(), NOW())
                    """,
                    (setup_id, instance_id, key, STEP_POSITION[key]),
                )
            conn.commit()
    return _pg_get_record(user_id, instance_id)


def _pg_claim_next_step(worker_id: str) -> dict | None:
    from psycopg.rows import dict_row

    with _pg_connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                WITH candidate AS (
                    SELECT s.id AS step_id
                    FROM email_agent_instance_setup_step s
                    JOIN email_agent_instance_setup p ON p.id = s.setup_id
                    WHERE s.status = 'pending'
                      AND p.status NOT IN ('ready', 'failed')
                      AND NOT EXISTS (
                          SELECT 1 FROM email_agent_instance_setup_step prev
                          WHERE prev.setup_id = s.setup_id
                            AND prev.position < s.position
                            AND prev.status NOT IN ('done', 'skipped', 'failed')
                      )
                    ORDER BY s.created_at ASC
                    FOR UPDATE OF s SKIP LOCKED
                    LIMIT 1
                )
                UPDATE email_agent_instance_setup_step
                SET status = 'running', claimed_by = %(worker_id)s, claimed_at = NOW(),
                    started_at = NOW(), attempts = attempts + 1, updated_at = NOW()
                FROM candidate
                WHERE email_agent_instance_setup_step.id = candidate.step_id
                RETURNING email_agent_instance_setup_step.id, email_agent_instance_setup_step.setup_id,
                    email_agent_instance_setup_step.step_key
                """,
                {"worker_id": worker_id},
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                "UPDATE email_agent_instance_setup SET status = 'running_setup', updated_at = NOW() "
                "WHERE id = %s AND status = 'created'",
                (row["setup_id"],),
            )
            cur.execute(
                "SELECT user_id, agent_instance_id FROM email_agent_instance_setup WHERE id = %s",
                (row["setup_id"],),
            )
            owner = cur.fetchone()
            conn.commit()
    return {
        "setup_id": row["setup_id"],
        "user_id": owner["user_id"],
        "agent_instance_id": owner["agent_instance_id"],
        "step_key": row["step_key"],
    }


def _pg_update_step(setup_id: int, step_key: str, patch: dict, *, fatal_failure: bool = False) -> None:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE email_agent_instance_setup_step
                SET status = %(status)s, error = %(error)s, detail = %(detail)s,
                    finished_at = CASE WHEN %(status)s IN ('done', 'skipped', 'failed')
                                       THEN NOW() ELSE finished_at END,
                    updated_at = NOW()
                WHERE setup_id = %(setup_id)s AND step_key = %(step_key)s
                """,
                {
                    "setup_id": setup_id,
                    "step_key": step_key,
                    "status": patch.get("status"),
                    "error": patch.get("error"),
                    "detail": json.dumps(patch.get("detail")) if patch.get("detail") is not None else None,
                },
            )
            if fatal_failure:
                cur.execute(
                    """
                    UPDATE email_agent_instance_setup
                    SET status = 'failed', error = %(error)s, finished_at = NOW(), updated_at = NOW()
                    WHERE id = %(setup_id)s
                    """,
                    {"setup_id": setup_id, "error": patch.get("error")},
                )
            conn.commit()


def _pg_requeue_stale(stale_seconds: float, max_attempts: int) -> int:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE email_agent_instance_setup_step
                SET status = CASE WHEN attempts >= %(max_attempts)s THEN 'abandoned' ELSE 'pending' END,
                    claimed_by = NULL, claimed_at = NULL, updated_at = NOW()
                WHERE status = 'running'
                  AND claimed_at < NOW() - make_interval(secs => %(stale_seconds)s)
                """,
                {"stale_seconds": stale_seconds, "max_attempts": max_attempts},
            )
            n = cur.rowcount
            conn.commit()
    return n


def _pg_mark_ready(setup_id: int) -> None:
    with _pg_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE email_agent_instance_setup SET status = 'ready', finished_at = NOW(), updated_at = NOW() "
                "WHERE id = %s",
                (setup_id,),
            )
            conn.commit()


# --- Public API ---

def start_setup(user_id: str | None = None, agent_instance_id: str | None = None, *, force: bool = False) -> dict:
    uid, iid = _resolve(user_id, agent_instance_id)
    if selected_run_registry_backend() == "postgres":
        record = _pg_start(uid, iid, force)
    else:
        record = _json_start(uid, iid, force)
    return _public(record)


def get_setup(user_id: str | None = None, agent_instance_id: str | None = None) -> dict:
    uid, iid = _resolve(user_id, agent_instance_id)
    if selected_run_registry_backend() == "postgres":
        record = _pg_get_record(uid, iid)
    else:
        record = _json_get_record(uid, iid)
    if record is None:
        return {
            "status": "not_started",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "steps": [],
            "progress": {"done": 0, "total": len(SETUP_STEPS), "percent": 0},
        }
    return _public(record)


def claim_next_step(worker_id: str) -> dict | None:
    if selected_run_registry_backend() == "postgres":
        return _pg_claim_next_step(worker_id)
    return _json_claim_next_step(worker_id)


def complete_step(setup_id, step_key: str, *, detail: dict | None = None) -> None:
    patch = {"status": "done", "error": None, "detail": detail}
    if selected_run_registry_backend() == "postgres":
        _pg_update_step(setup_id, step_key, patch)
    else:
        _json_update_step(str(setup_id), step_key, patch)


def skip_step(setup_id, step_key: str, reason: str) -> None:
    patch = {"status": "skipped", "error": None, "detail": {"reason": reason}}
    if selected_run_registry_backend() == "postgres":
        _pg_update_step(setup_id, step_key, patch)
    else:
        _json_update_step(str(setup_id), step_key, patch)


def fail_step(setup_id, step_key: str, error: str) -> None:
    message = public_error_message(error)
    patch = {"status": "failed", "error": message, "detail": None}
    fatal = step_key in FATAL_STEPS
    if selected_run_registry_backend() == "postgres":
        _pg_update_step(setup_id, step_key, patch, fatal_failure=fatal)
    else:
        _json_update_step(str(setup_id), step_key, patch, fatal_failure=fatal)


def requeue_stale_steps(stale_seconds: float | None = None, max_attempts: int | None = None) -> int:
    stale_seconds = settings.setup_stale_seconds if stale_seconds is None else stale_seconds
    max_attempts = settings.setup_max_attempts if max_attempts is None else max_attempts
    if selected_run_registry_backend() == "postgres":
        return _pg_requeue_stale(stale_seconds, max_attempts)
    return _json_requeue_stale(stale_seconds, max_attempts)


def mark_ready(setup_id) -> None:
    if selected_run_registry_backend() == "postgres":
        _pg_mark_ready(setup_id)
    else:
        _json_mark_ready(str(setup_id))


def retry_step(step_key: str, user_id: str | None = None, agent_instance_id: str | None = None) -> dict:
    """Reset one failed step to pending so the worker loop reclaims it.

    If the setup as a whole had terminated as 'failed' (a fatal step failed),
    un-terminate it so claim_next_step can pick the retried step up again."""
    uid, iid = _resolve(user_id, agent_instance_id)
    if selected_run_registry_backend() == "postgres":
        record = _pg_get_record(uid, iid)
        if record is None:
            return get_setup(uid, iid)
        with _pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE email_agent_instance_setup_step
                    SET status = 'pending', error = NULL, started_at = NULL, finished_at = NULL, updated_at = NOW()
                    WHERE setup_id = %s AND step_key = %s AND status = 'failed'
                    """,
                    (record["id"], step_key),
                )
                cur.execute(
                    """
                    UPDATE email_agent_instance_setup
                    SET status = 'running_setup', error = NULL, finished_at = NULL, updated_at = NOW()
                    WHERE id = %s AND status = 'failed'
                    """,
                    (record["id"],),
                )
                conn.commit()
        return get_setup(uid, iid)
    record = _json_get_record(uid, iid)
    if record is None:
        return get_setup(uid, iid)
    for step in record["steps"]:
        if step["step_key"] == step_key and step["status"] == "failed":
            step["status"] = "pending"
            step["error"] = None
            step["started_at"] = None
            step["finished_at"] = None
    if record["status"] == "failed":
        record["status"] = "running_setup"
        record["error"] = None
        record["finished_at"] = None
    _json_save_record(uid, iid, record)
    return get_setup(uid, iid)


def force_ready(user_id: str | None = None, agent_instance_id: str | None = None) -> dict:
    """Skip onboarding: mark the instance ready without touching step states.

    For a power user reconnecting a known-good mailbox who should not be
    forced through the setup gate."""
    uid, iid = _resolve(user_id, agent_instance_id)
    if selected_run_registry_backend() == "postgres":
        record = _pg_get_record(uid, iid)
        if record is None:
            start_setup(uid, iid)
            record = _pg_get_record(uid, iid)
        _pg_mark_ready(record["id"])
    else:
        record = _json_get_record(uid, iid)
        if record is None:
            start_setup(uid, iid)
        _json_mark_ready(f"{uid}::{iid}")
    return get_setup(uid, iid)


# --- Step handlers ---

def _extract_sender(message: dict) -> tuple[str, str]:
    name, email = parseaddr(message.get("from", ""))
    return (email or "").strip().lower(), (name or "").strip()


async def _to_thread_with_timeout(func, *args, step_label: str):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args),
            timeout=settings.setup_llm_step_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise SkipStep(f"{step_label} timed out") from exc


def _provider(context: SetupContext):
    """The mail provider for this setup run, built once and reused across steps.

    `_step_verify_provider` normally fills it in first; steps can still be
    retried individually, so this rebuilds rather than assuming it is set.
    """
    from src.mail import get_provider

    if context.provider is None:
        context.provider = get_provider(agent_instance_id=context.agent_instance_id)
    return context.provider


async def _step_verify_provider(context: SetupContext) -> dict:
    from src.mail import get_provider

    provider = get_provider(agent_instance_id=context.agent_instance_id)
    result = await asyncio.to_thread(provider.probe)
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Could not reach the mailbox.")
    context.provider = provider
    return {"email_address": result.get("mailbox")}


async def _step_fetch_recent(context: SetupContext) -> dict:
    runtime = load_runtime_settings(context.agent_instance_id)
    messages = await asyncio.to_thread(_provider(context).fetch_recent, runtime.setup_recent_limit)
    context.recent_messages = messages
    return {"messages_fetched": len(messages)}


async def _step_import_contacts(context: SetupContext) -> dict:
    if not context.recent_messages:
        raise SkipStep("no messages fetched")
    from src.contacts import Contact, upsert_contact
    from src.junk_gate import is_junk

    seen: dict[str, tuple[str, dict]] = {}
    for message in context.recent_messages:
        email, name = _extract_sender(message)
        if email and "@" in email:
            seen.setdefault(email, (name, message))
    imported = 0
    for email, (name, message) in seen.items():
        try:
            junk, _reason = is_junk({
                "author": message.get("from", ""),
                "labels": message.get("labels", []),
                "list_unsubscribe": message.get("list_unsubscribe"),
                "precedence_bulk": message.get("precedence_bulk"),
                "list_id": message.get("list_id"),
                "auto_submitted": message.get("auto_submitted"),
            })
            contact_payload = {
                "email": email,
                "name": name or None,
                "audience": "prospect",
                "tags": ["imported"] if not junk else ["imported", "bulk"],
            }
            if not junk:
                contact_payload.update({
                    "category": "externe",
                    "category_source": "imported",
                    "category_confidence": 0.55,
                })
            await asyncio.to_thread(
                upsert_contact,
                Contact(**contact_payload),
                context.agent_instance_id,
            )
            imported += 1
        except Exception:
            continue
    return {"contacts_imported": imported}


async def _step_learn_style(context: SetupContext) -> dict:
    from src.config import load_config

    cfg = load_config()
    if not cfg.style_learning.enabled:
        raise SkipStep("style learning disabled")
    if not context.sent_samples:
        context.sent_samples = await asyncio.to_thread(
            _provider(context).fetch_sent,
            load_runtime_settings(context.agent_instance_id).setup_sent_sample,
        )
    if not context.sent_samples:
        raise SkipStep("no sent mail sampled")
    from src import graph as graph_module
    from src.memory import ORIGIN_SETUP, namespace, wrap_preferences
    from src.style_learning import analyze_style, build_style_text

    profile = await _to_thread_with_timeout(
        analyze_style, context.sent_samples, graph_module.llm, step_label="style learning"
    )
    text = build_style_text(profile)
    if context.store is not None:
        await context.store.aput(
            namespace("writing_style", context.user_id, context.agent_instance_id),
            "user_preferences",
            wrap_preferences(text, ORIGIN_SETUP),
        )
    return {"sample_count": len(context.sent_samples), "writing_style": text}


# Tone/persona inference needs only a handful of examples, but
# context.recent_messages carries whatever AGENT_SETUP_RECENT_LIMIT is set
# to (200 by default) — passed through unfiltered, that reliably blew past
# a local model's context window (10k+ tokens against a 4k window is not
# an edge case, it is every mailbox with real volume). Capped independently
# of the fetch limit so raising that setting for other steps can't reopen this.
MAX_SUGGEST_RECENT_MESSAGES = 20
MAX_SUGGEST_SENT_SAMPLES = 8


async def _step_suggest_persona(context: SetupContext) -> dict:
    from src.persona import load_persona, suggest_persona

    persona = await asyncio.to_thread(load_persona, context.agent_instance_id)
    if not persona.is_empty():
        raise SkipStep("persona already customised")
    if not context.sent_samples and not context.recent_messages:
        raise SkipStep("no mailbox samples available")
    from src import graph as graph_module

    suggestion = await _to_thread_with_timeout(
        suggest_persona,
        context.sent_samples[:MAX_SUGGEST_SENT_SAMPLES],
        context.recent_messages[:MAX_SUGGEST_RECENT_MESSAGES],
        graph_module.llm,
        step_label="persona suggestion",
    )
    return {"suggestion": suggestion.model_dump()}


async def _step_detect_signature(context: SetupContext) -> dict:
    if not context.sent_samples:
        raise SkipStep("no sent mail sampled")
    from src.signature import detect_from_sent, load_signature, save_signature

    result = await asyncio.to_thread(detect_from_sent, context.sent_samples)
    if result.get("detected") and result.get("confidence", 0) >= 0.7:
        signature = await asyncio.to_thread(load_signature, context.agent_instance_id)
        signature.mode = "preserve_provider_signature"
        signature.detected_block = result.get("block")
        await asyncio.to_thread(save_signature, signature, context.agent_instance_id)
    return result


async def _step_seed_categories(context: SetupContext) -> dict:
    from src.automation import RuleWhen
    from src.categories import Category, DEFAULT_CATEGORIES_PATH, Template, dump_categories, load_categories
    from src.instance_config import write_instance_text

    cfg = await asyncio.to_thread(load_categories, None, context.agent_instance_id)
    if cfg.categories:
        raise SkipStep("categories already customised")
    cfg.enabled = True
    cfg.categories = [
        Category(
            name="clients",
            display_name="Clients",
            enabled=True,
            priority="normal",
            policy="auto_draft",
            template="client_reply",
            when=RuleWhen(body_contains=["devis", "proposition", "contrat", "rendez-vous", "rdv", "question"]),
        ),
        Category(
            name="externe",
            display_name="Externe",
            enabled=True,
            priority="normal",
            policy="auto_draft",
            template="external_reply",
        ),
        Category(
            name="interne",
            display_name="Interne",
            enabled=True,
            priority="normal",
            policy="notify",
            external_send_allowed=False,
        ),
        Category(
            name="fournisseurs",
            display_name="Fournisseurs",
            enabled=True,
            priority="normal",
            policy="notify",
            when=RuleWhen(body_contains=["facture", "livraison", "commande", "paiement"]),
        ),
        Category(
            name="general",
            display_name="Général",
            enabled=True,
            priority="low",
            policy="notify",
        ),
    ]
    cfg.templates = [
        Template(
            name="client_reply",
            subject="Re: {{subject}}",
            body=(
                "Bonjour {{prenom}}\n\n"
                "Merci pour votre message. Je reviens vers vous rapidement avec les éléments adaptés.\n\n"
                "Cordialement"
            ),
            variables=["subject", "prenom"],
        ),
        Template(
            name="external_reply",
            subject="Re: {{subject}}",
            body=(
                "Bonjour {{prenom}}\n\n"
                "Merci pour votre message. J'ai bien pris connaissance de votre demande et je vous réponds rapidement.\n\n"
                "Cordialement"
            ),
            variables=["subject", "prenom"],
        ),
    ]
    await asyncio.to_thread(
        write_instance_text, "categories", dump_categories(cfg), DEFAULT_CATEGORIES_PATH, context.agent_instance_id
    )
    return {"categories_seeded": len(cfg.categories), "templates_seeded": len(cfg.templates)}


async def _step_triage_backlog(context: SetupContext) -> dict:
    provider = _provider(context)

    if not settings.job_queue_enabled:
        from src import graph as graph_module
        from src.automation import load_rules
        from src.poller import process_message_with_retry

        refs = await asyncio.to_thread(
            provider.fetch_unread, load_runtime_settings(context.agent_instance_id).setup_backlog_limit
        )
        rules_config = load_rules()
        outcomes = []
        timed_out = 0
        for ref in refs:
            msg_id = ref.get("id")
            if not msg_id:
                continue
            try:
                outcome = await asyncio.wait_for(
                    process_message_with_retry(graph_module.graph, msg_id, provider, rules_config),
                    timeout=settings.setup_backlog_message_timeout_seconds,
                )
            except asyncio.TimeoutError:
                timed_out += 1
                outcomes.append((msg_id, "setup_timeout", ""))
                continue
            outcomes.append(outcome)
        return {
            "backlog_seen": len(refs),
            "backlog_processed": len([outcome for outcome in outcomes if outcome[1] != "setup_timeout"]),
            "backlog_timed_out": timed_out,
            "outcomes": [
                {"message_id": msg_id, "status": status, "run_id": run_id}
                for msg_id, status, run_id in outcomes
            ],
        }

    from src.job_queue import enqueue_job

    refs = await asyncio.to_thread(
        provider.fetch_unread, load_runtime_settings(context.agent_instance_id).setup_backlog_limit
    )
    enqueued = 0
    for ref in refs:
        job = await asyncio.to_thread(enqueue_job, context.agent_instance_id, ref["id"])
        if job is not None:
            enqueued += 1
    return {"backlog_enqueued": enqueued}


async def _seed_default_memory(context: SetupContext) -> list[str]:
    """Write the config-derived preferences into the store at the end of setup.

    `get_memory` writes the config default lazily, on first read — and the first
    read only happens when the agent processes an email. So a freshly created
    instance showed an empty Memory page until some mail arrived, which reads as
    "the agent has not been configured" rather than "the agent is using the
    defaults you set". Seeding here makes the instance's starting position
    visible the moment setup finishes.

    Anything already written (by the style step, or by a human) is left alone.
    """
    if context.store is None:
        return []

    from src.config import load_config
    from src.memory import ORIGIN_DEFAULT, namespace, wrap_preferences

    cfg = load_config()
    defaults = {
        "triage_preferences": cfg.agent.triage_instructions,
        "response_preferences": cfg.agent.response_preferences,
    }

    seeded: list[str] = []
    for key, content in defaults.items():
        if not content:
            continue
        ns = namespace(key, context.user_id, context.agent_instance_id)
        existing = await context.store.aget(ns, "user_preferences")
        if existing is not None:
            continue
        await context.store.aput(ns, "user_preferences", wrap_preferences(content, ORIGIN_DEFAULT))
        seeded.append(key)
    return seeded


async def _step_finalize(context: SetupContext) -> dict:
    seeded_memory: list[str] = []
    try:
        seeded_memory = await _seed_default_memory(context)
    except Exception as exc:
        # Never fail setup over this: the lazy path in get_memory still applies.
        logger.warning(f"instance_setup: memory seeding failed: {exc}")

    try:
        from src.notification_store import create_notification

        create_notification(
            notification_type="setup_completed",
            title="Configuration terminée",
            body="La configuration de l'instance est terminée.",
            severity="success",
            user_id=context.user_id,
            agent_instance_id=context.agent_instance_id,
        )
    except Exception as exc:
        logger.warning(f"instance_setup: notification emission failed: {exc}")
    return {"seeded_memory": seeded_memory}


STEP_HANDLERS: dict[str, Callable[[SetupContext], Awaitable[dict]]] = {
    "verify_provider": _step_verify_provider,
    "fetch_recent": _step_fetch_recent,
    "import_contacts": _step_import_contacts,
    "learn_style": _step_learn_style,
    "suggest_persona": _step_suggest_persona,
    "detect_signature": _step_detect_signature,
    "seed_categories": _step_seed_categories,
    "triage_backlog": _step_triage_backlog,
    "finalize": _step_finalize,
}


async def run_step(step: dict, context: SetupContext) -> None:
    """Run one claimed step. Never lets an exception escape into the caller's loop."""
    setup_id = step["setup_id"]
    step_key = step["step_key"]
    handler = STEP_HANDLERS.get(step_key)
    if handler is None:
        fail_step(setup_id, step_key, f"no handler registered for step {step_key}")
        return
    try:
        detail = await handler(context)
        complete_step(setup_id, step_key, detail=detail)
    except SkipStep as exc:
        skip_step(setup_id, step_key, exc.reason)
    except Exception as exc:
        logger.warning(f"instance_setup: step {step_key} ({setup_id}) failed: {exc}")
        fail_step(setup_id, step_key, str(exc))
        return
    if step_key == "finalize":
        mark_ready(setup_id)


async def run_pipeline_inline(user_id: str | None = None, agent_instance_id: str | None = None, *, store: Any = None) -> None:
    """Run every claimable step for one instance to completion, in-process.

    Used when the job queue is disabled (AGENT_JOB_QUEUE_ENABLED=false, the dev
    default) so local dev without a worker process still reaches 'ready'."""
    uid, iid = _resolve(user_id, agent_instance_id)
    context = SetupContext(user_id=uid, agent_instance_id=iid, store=store)
    worker_id = "inline"
    guard = 0
    while guard < len(SETUP_STEPS) + 1:
        guard += 1
        step = claim_next_step(worker_id)
        if step is None:
            break
        if step["user_id"] != uid or step["agent_instance_id"] != iid:
            # Another instance's step got claimed first (shared JSON/pg claim pool);
            # nothing to do here for this call, another caller owns it.
            continue
        with user_context(uid):
            with agent_instance_context(iid):
                await run_step(step, context)
        status = get_setup(uid, iid).get("status")
        if status in TERMINAL_STATUSES:
            break
