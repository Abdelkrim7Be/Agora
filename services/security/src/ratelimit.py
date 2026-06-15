from __future__ import annotations

import time

# Module-level in-process state. Fine for single-user dev; move to Redis/DB before
# multi-process prod. Clock is monkeypatchable for deterministic tests.
_per_run: dict[str, int] = {}
_per_day: list[float] = []
_now = time.time


def would_exceed(
    run_id: str,
    max_per_run: int | None,
    max_per_day: int | None,
) -> str | None:
    """Return a deny-reason if granting one more send would exceed a cap, else None."""
    if max_per_run is not None and _per_run.get(run_id, 0) >= max_per_run:
        return f"per-run send cap reached ({max_per_run})"
    if max_per_day is not None:
        cutoff = _now() - 86400
        recent = [t for t in _per_day if t >= cutoff]
        if len(recent) >= max_per_day:
            return f"per-day send cap reached ({max_per_day})"
    return None


def record(run_id: str) -> None:
    """Consume one rate-limit slot for this run (called on grant, before HITL)."""
    _per_run[run_id] = _per_run.get(run_id, 0) + 1
    _per_day.append(_now())


def reset() -> None:
    """Clear all counters. Called by the autouse test fixture."""
    _per_run.clear()
    _per_day.clear()
