from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from src.config import settings
from src.metrics import inc_counter, register_metric
from src.shared_cache import window_add, window_clear, window_count
from src.tenant import current_agent_instance_id, normalize_agent_instance_id

_WINDOW_SECONDS = 3600.0
_ALERT_RATIO = 0.8


def _window_key(instance_id: str) -> str:
    return f"agora:gmail-budget:{instance_id}"

_lock = threading.Lock()
_calls: dict[str, deque] = defaultdict(deque)
# One alert per instance per window crossing — avoids log spam every call.
_alerted: dict[str, float] = {}

register_metric("gmail_api_calls_total", "counter", "Gmail API calls made, by agent instance")
register_metric("gmail_budget_alerts_total", "counter", "Times an instance crossed 80% of its hourly Gmail budget")


def _prune(dq: deque, now: float) -> None:
    cutoff = now - _WINDOW_SECONDS
    while dq and dq[0] < cutoff:
        dq.popleft()


def record_gmail_call(count: int = 1, agent_instance_id: str | None = None) -> None:
    """Record `count` Gmail API calls against the current instance's hourly budget."""
    if count <= 0:
        return
    instance_id = normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())
    now = time.time()
    # The poller makes most of the Gmail calls but the API renders the tile, so a
    # per-process count reported a fraction of the real usage against the quota.
    shared = window_add(_window_key(instance_id), count, _WINDOW_SECONDS)
    with _lock:
        dq = _calls[instance_id]
        _prune(dq, now)
        for _ in range(count):
            dq.append(now)
        used = shared if shared is not None else len(dq)
    inc_counter("gmail_api_calls_total", amount=count, instance=instance_id)
    budget = settings.gmail_hourly_call_budget
    if budget > 0 and used >= budget * _ALERT_RATIO:
        with _lock:
            last = _alerted.get(instance_id, 0.0)
            fresh = now - last > _WINDOW_SECONDS
            if fresh:
                _alerted[instance_id] = now
        if fresh:
            print(f"poller: {instance_id} at {used}/{budget} Gmail calls this hour (>= 80% budget)")
            inc_counter("gmail_budget_alerts_total", instance=instance_id)


def calls_last_hour(agent_instance_id: str | None = None) -> int:
    instance_id = normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())
    shared = window_count(_window_key(instance_id), _WINDOW_SECONDS)
    if shared is not None:
        return shared
    now = time.time()
    with _lock:
        dq = _calls.get(instance_id)
        if not dq:
            return 0
        _prune(dq, now)
        return len(dq)


def budget_status(agent_instance_id: str | None = None) -> dict:
    used = calls_last_hour(agent_instance_id)
    budget = settings.gmail_hourly_call_budget
    pct = round(100 * used / budget) if budget > 0 else 0
    return {"calls_last_hour": used, "budget": budget, "pct": pct}


def reset_budget() -> None:
    """Test helper: clear all recorded calls and alert state."""
    with _lock:
        instance_ids = list(_calls)
        _calls.clear()
        _alerted.clear()
    for instance_id in instance_ids:
        window_clear(_window_key(instance_id))
