from __future__ import annotations

import time
import uuid

from src.config import settings

# Module-level in-process state. Fine for single-user dev. Clock is monkeypatchable
# for deterministic tests.
_per_run: dict[str, int] = {}
_per_day: list[float] = []
_reservations: set[tuple[str, str]] = set()
_now = time.time
_REDIS_PREFIX = "agora:security:ratelimit"


def selected_backend() -> str:
    backend = settings.ratelimit_backend.lower().strip()
    if backend not in {"memory", "redis"}:
        raise RuntimeError(f"Unsupported SECURITY_RATELIMIT_BACKEND: {settings.ratelimit_backend}")
    if backend == "redis" and not settings.redis_url:
        raise RuntimeError("REDIS_URL is required when SECURITY_RATELIMIT_BACKEND=redis")
    return backend


def _redis_client():
    try:
        from redis import Redis
    except ImportError as exc:
        raise RuntimeError("Redis rate limits require the redis package.") from exc
    return Redis.from_url(settings.redis_url, decode_responses=True)


def would_exceed(
    run_id: str,
    max_per_run: int | None,
    max_per_day: int | None,
    action_id: str = "",
) -> str | None:
    """Return a deny-reason if granting one more send would exceed a cap, else None."""
    if selected_backend() == "redis":
        return _redis_would_exceed(run_id, max_per_run, max_per_day, action_id)
    return _memory_would_exceed(run_id, max_per_run, max_per_day, action_id)


def _memory_would_exceed(
    run_id: str,
    max_per_run: int | None,
    max_per_day: int | None,
    action_id: str = "",
) -> str | None:
    if action_id and (run_id, action_id) in _reservations:
        return None
    if max_per_run is not None and _per_run.get(run_id, 0) >= max_per_run:
        return f"per-run send cap reached ({max_per_run})"
    if max_per_day is not None:
        cutoff = _now() - 86400
        recent = [t for t in _per_day if t >= cutoff]
        if len(recent) >= max_per_day:
            return f"per-day send cap reached ({max_per_day})"
    return None


def record(run_id: str, action_id: str = "") -> None:
    """Consume one rate-limit slot for this run (called on grant, before HITL)."""
    if selected_backend() == "redis":
        _redis_record(run_id, action_id)
        return
    _memory_record(run_id, action_id)


def _memory_record(run_id: str, action_id: str = "") -> None:
    if action_id:
        key = (run_id, action_id)
        if key in _reservations:
            return
        _reservations.add(key)
    _per_run[run_id] = _per_run.get(run_id, 0) + 1
    _per_day.append(_now())


def _redis_would_exceed(
    run_id: str,
    max_per_run: int | None,
    max_per_day: int | None,
    action_id: str = "",
) -> str | None:
    client = _redis_client()
    if action_id and client.exists(_reservation_key(run_id, action_id)):
        return None
    if max_per_run is not None:
        current = int(client.get(_run_key(run_id)) or 0)
        if current >= max_per_run:
            return f"per-run send cap reached ({max_per_run})"
    if max_per_day is not None:
        cutoff = _now() - 86400
        day_key = _day_key()
        client.zremrangebyscore(day_key, 0, cutoff)
        if client.zcard(day_key) >= max_per_day:
            return f"per-day send cap reached ({max_per_day})"
    return None


def _redis_record(run_id: str, action_id: str = "") -> None:
    client = _redis_client()
    if action_id:
        reservation_key = _reservation_key(run_id, action_id)
        if not client.set(reservation_key, "1", nx=True, ex=86400):
            return
    run_key = _run_key(run_id)
    client.incr(run_key)
    client.expire(run_key, 86400)
    now = _now()
    day_key = _day_key()
    client.zadd(day_key, {f"{now}:{uuid.uuid4()}": now})
    client.zremrangebyscore(day_key, 0, now - 86400)
    client.expire(day_key, 86400)


def _run_key(run_id: str) -> str:
    return f"{_REDIS_PREFIX}:run:{run_id}"


def _reservation_key(run_id: str, action_id: str) -> str:
    return f"{_REDIS_PREFIX}:reservation:{run_id}:{action_id}"


def _day_key() -> str:
    return f"{_REDIS_PREFIX}:day"


def reset() -> None:
    """Clear all counters. Called by the autouse test fixture."""
    _per_run.clear()
    _per_day.clear()
    _reservations.clear()
    if settings.ratelimit_backend.lower().strip() == "redis" and settings.redis_url:
        client = _redis_client()
        for key in client.scan_iter(f"{_REDIS_PREFIX}:*"):
            client.delete(key)
