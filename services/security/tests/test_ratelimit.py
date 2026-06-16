from __future__ import annotations

import src.ratelimit as ratelimit
from src.config import settings


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    def exists(self, key: str) -> bool:
        return key in self.values

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def incr(self, key: str) -> int:
        value = int(self.values.get(key) or 0) + 1
        self.values[key] = str(value)
        return value

    def expire(self, key: str, ttl: int) -> bool:
        return True

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zremrangebyscore(self, key: str, minimum: float, maximum: float) -> int:
        zset = self.zsets.setdefault(key, {})
        removed = [member for member, score in zset.items() if minimum <= score <= maximum]
        for member in removed:
            zset.pop(member)
        return len(removed)

    def zcard(self, key: str) -> int:
        return len(self.zsets.setdefault(key, {}))

    def scan_iter(self, pattern: str):
        prefix = pattern.rstrip("*")
        for key in list(self.values.keys()):
            if key.startswith(prefix):
                yield key
        for key in list(self.zsets.keys()):
            if key.startswith(prefix):
                yield key

    def delete(self, key: str) -> int:
        existed = key in self.values or key in self.zsets
        self.values.pop(key, None)
        self.zsets.pop(key, None)
        return int(existed)


def test_selected_backend_defaults_to_memory(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ratelimit_backend", "memory")

    assert ratelimit.selected_backend() == "memory"


def test_selected_backend_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ratelimit_backend", "sqlite")

    try:
        ratelimit.selected_backend()
    except RuntimeError as exc:
        assert "Unsupported SECURITY_RATELIMIT_BACKEND" in str(exc)
    else:
        raise AssertionError("expected backend validation to fail")


def test_selected_backend_requires_redis_url(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ratelimit_backend", "redis")
    monkeypatch.setattr(settings, "redis_url", "")

    try:
        ratelimit.selected_backend()
    except RuntimeError as exc:
        assert "REDIS_URL is required" in str(exc)
    else:
        raise AssertionError("expected redis URL validation to fail")


def test_redis_backend_enforces_run_cap_and_idempotent_action(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr(ratelimit, "_redis_client", lambda: fake)
    monkeypatch.setattr(ratelimit, "_now", lambda: 1000.0)

    assert ratelimit._redis_would_exceed("run-1", 1, None, "act-1") is None
    ratelimit._redis_record("run-1", "act-1")

    assert ratelimit._redis_would_exceed("run-1", 1, None, "act-1") is None
    ratelimit._redis_record("run-1", "act-1")
    assert fake.get(ratelimit._run_key("run-1")) == "1"

    assert ratelimit._redis_would_exceed("run-1", 1, None, "act-2") == (
        "per-run send cap reached (1)"
    )


def test_redis_backend_enforces_day_cap(monkeypatch) -> None:
    fake = FakeRedis()
    now = 1000.0
    monkeypatch.setattr(ratelimit, "_redis_client", lambda: fake)
    monkeypatch.setattr(ratelimit, "_now", lambda: now)

    assert ratelimit._redis_would_exceed("run-1", None, 1) is None
    ratelimit._redis_record("run-1")

    assert ratelimit._redis_would_exceed("run-2", None, 1) == (
        "per-day send cap reached (1)"
    )
