"""The API and the poller are separate processes against one mailbox, so the
Gmail call budget and the inbox listing cache have to be shared or they are
wrong. Everything here also has to keep working with no broker at all."""

from __future__ import annotations

import time

import pytest

from src import gmail_budget, shared_cache
from src.config import settings


class FakeRedis:
    """Enough of the client for the two access patterns used here."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.pinged = False

    def ping(self):
        self.pinged = True
        return True

    # --- strings ---
    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
            self.zsets.pop(key, None)

    def scan_iter(self, match="*", count=None):
        prefix = match.rstrip("*")
        return [key for key in list(self.values) if key.startswith(prefix)]

    # --- sorted sets ---
    def zremrangebyscore(self, key, low, high):
        members = self.zsets.setdefault(key, {})
        for member in [m for m, score in members.items() if low <= score <= high]:
            members.pop(member)

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def expire(self, key, seconds):
        return True

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, client):
        self.client = client
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return record

    def execute(self):
        return [getattr(self.client, name)(*args, **kwargs) for name, args, kwargs in self.calls]


@pytest.fixture
def fake_redis(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(settings, "redis_url", "redis://fake:6379/0")
    monkeypatch.setattr(shared_cache, "_connect", lambda: client)
    shared_cache.reset_shared_cache()
    yield client
    shared_cache.reset_shared_cache()


@pytest.fixture(autouse=True)
def _no_leftover_client():
    shared_cache.reset_shared_cache()
    yield
    shared_cache.reset_shared_cache()


def test_without_redis_url_everything_stays_local(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    shared_cache.reset_shared_cache()

    assert shared_cache.redis_client() is None
    assert shared_cache.cache_get_json("agora:inbox:x") is None
    assert shared_cache.cache_set_json("agora:inbox:x", [1], 60) is False
    assert shared_cache.window_add("agora:budget:x", 3, 3600) is None
    assert shared_cache.window_count("agora:budget:x", 3600) is None


def test_json_values_round_trip_and_clear_by_prefix(fake_redis):
    shared_cache.cache_set_json("agora:inbox:u:i:inbox:25", [{"id": "1"}], 60)
    assert shared_cache.cache_get_json("agora:inbox:u:i:inbox:25") == [{"id": "1"}]

    shared_cache.cache_delete_prefix("agora:inbox:u:i:")
    assert shared_cache.cache_get_json("agora:inbox:u:i:inbox:25") is None


def test_window_counts_every_call_not_one_per_tick(fake_redis):
    assert shared_cache.window_add("agora:budget:i", 3, 3600) == 3
    assert shared_cache.window_add("agora:budget:i", 2, 3600) == 5
    assert shared_cache.window_count("agora:budget:i", 3600) == 5


def test_window_drops_events_older_than_the_span(fake_redis):
    shared_cache.window_add("agora:budget:i", 2, 3600)
    for member in fake_redis.zsets["agora:budget:i"]:
        fake_redis.zsets["agora:budget:i"][member] = time.time() - 7200

    assert shared_cache.window_count("agora:budget:i", 3600) == 0


def test_a_broken_broker_degrades_instead_of_raising(monkeypatch):
    def explode():
        raise ConnectionError("no route to host")

    monkeypatch.setattr(settings, "redis_url", "redis://down:6379/0")
    monkeypatch.setattr(shared_cache, "_connect", explode)
    shared_cache.reset_shared_cache()

    assert shared_cache.redis_client() is None
    assert shared_cache.cache_get_json("agora:inbox:x") is None


def test_gmail_budget_counts_calls_made_by_another_process(fake_redis, monkeypatch):
    """The poller's calls have to show up in the API's budget tile."""
    gmail_budget.reset_budget()
    key = gmail_budget._window_key("default-email-agent")
    # Stand in for the other container recording 40 calls against the same window.
    shared_cache.window_add(key, 40, 3600)

    gmail_budget.record_gmail_call(2, agent_instance_id="default-email-agent")

    assert gmail_budget.calls_last_hour("default-email-agent") == 42


def test_gmail_budget_falls_back_to_process_local_counting(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    shared_cache.reset_shared_cache()
    gmail_budget.reset_budget()

    gmail_budget.record_gmail_call(4, agent_instance_id="default-email-agent")

    assert gmail_budget.calls_last_hour("default-email-agent") == 4
