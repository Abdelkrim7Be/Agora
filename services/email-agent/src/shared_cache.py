"""Process-shared cache backed by Redis, with an in-process fallback.

The API and the poller are separate containers that both talk to the same
mailbox. Anything they each kept in a module-level dict was therefore counted or
cached twice: the Gmail hourly budget only ever showed the API's own calls (so
the tile under-reported while the poller did most of the work), and the inbox
listing cache was warmed in one process and missed in the other.

Redis is optional. With `REDIS_URL` unset — or unreachable — every function here
degrades to the same per-process behaviour that existed before, so local dev and
tests need no broker.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
_client: Any = None
_client_resolved = False
# Set after a failed round trip so one dead broker does not add its timeout to
# every later request; cleared by reset_shared_cache().
_client_broken = False


def _connect():
    from redis import Redis

    client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=1.5,
        socket_connect_timeout=1.5,
    )
    client.ping()
    return client


def redis_client():
    """The shared client, or None when Redis is not configured or not reachable."""
    global _client, _client_resolved, _client_broken
    if not settings.redis_url or _client_broken:
        return None
    if _client_resolved:
        return _client
    with _client_lock:
        if _client_resolved:
            return _client
        try:
            _client = _connect()
        except Exception as exc:
            logger.warning("shared cache: Redis unavailable (%s); using per-process state", exc)
            _client = None
            _client_broken = True
        _client_resolved = True
    return _client


def reset_shared_cache() -> None:
    """Test helper: forget the resolved client so settings changes take effect."""
    global _client, _client_resolved, _client_broken
    with _client_lock:
        _client = None
        _client_resolved = False
        _client_broken = False


def _degrade(exc: Exception) -> None:
    """A broker that answers wrongly must never break a request — drop to local."""
    global _client_broken
    logger.warning("shared cache: Redis call failed (%s); using per-process state", exc)
    _client_broken = True


# --- JSON values with a TTL -------------------------------------------------

def cache_get_json(key: str) -> Any | None:
    client = redis_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
    except Exception as exc:
        _degrade(exc)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def cache_set_json(key: str, value: Any, ttl_seconds: float) -> bool:
    client = redis_client()
    if client is None or ttl_seconds <= 0:
        return False
    try:
        client.set(key, json.dumps(value), ex=max(1, int(ttl_seconds)))
        return True
    except Exception as exc:
        _degrade(exc)
        return False


def cache_delete_prefix(prefix: str) -> bool:
    """Delete every key under a prefix. SCAN, not KEYS: this runs on the request
    path when a mailbox mutation invalidates its listings."""
    client = redis_client()
    if client is None:
        return False
    try:
        for key in client.scan_iter(match=f"{prefix}*", count=200):
            client.delete(key)
        return True
    except Exception as exc:
        _degrade(exc)
        return False


# --- Sliding window counters ------------------------------------------------

def window_add(key: str, amount: int, window_seconds: float) -> int | None:
    """Record `amount` events now and return the count still inside the window."""
    client = redis_client()
    if client is None or amount <= 0:
        return None
    now = time.time()
    try:
        pipe = client.pipeline()
        pipe.zremrangebyscore(key, 0, now - window_seconds)
        # Members must be unique or a burst inside one clock tick collapses into
        # a single entry; the score is what the window is actually read from.
        for index in range(amount):
            pipe.zadd(key, {f"{now:.6f}:{index}": now})
        pipe.expire(key, max(1, int(window_seconds)))
        pipe.zcard(key)
        return int(pipe.execute()[-1])
    except Exception as exc:
        _degrade(exc)
        return None


def window_count(key: str, window_seconds: float) -> int | None:
    client = redis_client()
    if client is None:
        return None
    try:
        pipe = client.pipeline()
        pipe.zremrangebyscore(key, 0, time.time() - window_seconds)
        pipe.zcard(key)
        return int(pipe.execute()[-1])
    except Exception as exc:
        _degrade(exc)
        return None


def window_clear(key: str) -> None:
    client = redis_client()
    if client is None:
        return
    try:
        client.delete(key)
    except Exception as exc:
        _degrade(exc)
