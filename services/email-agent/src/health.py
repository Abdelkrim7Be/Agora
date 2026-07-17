from __future__ import annotations

import socket
from urllib.parse import urlparse

import httpx

from src.config import settings
from src.run_registry import ACTIVE_RUN_STATUSES, list_runs
from src.sync_status import get_status as get_sync_status
from src.sync_status import latest_success_at
from src.tenant import current_agent_instance_id

PROBE_TIMEOUT_SECONDS = 2.0


def _poller_component() -> dict:
    """Read-only: derives poller state from the last recorded sync_status, never
    probes the poller process directly (it may be a separate container)."""
    status = get_sync_status()
    last_success = status.get("last_success_at")
    last_failure = status.get("last_failure_at")
    paused = bool(status.get("paused", False))
    up = not paused and (last_failure is None or (last_success and last_success > last_failure))
    # Global page has no instance context: surface the freshest poll across
    # every instance so an active mailbox isn't reported with a stale timestamp.
    try:
        global_success = latest_success_at()
    except Exception:
        global_success = None
    display_last = max(filter(None, [last_success, global_success]), default=None)
    return {
        "status": "paused" if paused else ("up" if up else "down"),
        "last_poll_at": display_last,
        "last_error": status.get("last_error") if not up and not paused else None,
    }


async def _security_component() -> dict:
    if not settings.security_enabled:
        return {"status": "disabled"}
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{settings.security_url}/health")
        return {"status": "up" if resp.status_code < 400 else "down"}
    except Exception:
        return {"status": "down"}


def _database_component() -> dict:
    if not settings.database_url:
        return {"status": "disabled", "detail": "local sqlite/json backend"}
    try:
        import psycopg

        with psycopg.connect(settings.database_url, connect_timeout=int(PROBE_TIMEOUT_SECONDS)) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return {"status": "up"}
    except Exception:
        return {"status": "down"}


def _redis_component() -> dict:
    if not settings.redis_url:
        return {"status": "disabled"}
    try:
        parsed = urlparse(settings.redis_url)
        host, port = parsed.hostname, parsed.port or 6379
        if not host:
            return {"status": "down"}
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS):
            pass
        return {"status": "up"}
    except Exception:
        return {"status": "down"}


def _queue_depth() -> int:
    try:
        return sum(
            len(list_runs(status=s, user_id=None, agent_instance_id=current_agent_instance_id()))
            for s in ACTIVE_RUN_STATUSES
        )
    except Exception:
        return 0


async def aggregate_health() -> dict:
    """Never raises and never blocks long: each probe is time-boxed and
    exception-guarded so one down component reports 'down', not a 500."""
    return {
        "agent": {"status": "up"},
        "poller": _poller_component(),
        "security": await _security_component(),
        "database": _database_component(),
        "redis": _redis_component(),
        "queue_depth": _queue_depth(),
        "notifications": {"enabled": settings.notify_enabled},
    }
