from __future__ import annotations

import os
import threading

import pytest

from src.config import settings
from src.job_queue import (
    claim_job,
    count_jobs,
    enqueue_job,
    mark_job_done,
    require_job_queue_available,
    requeue_stale_jobs,
)
from src.migrate import upgrade_to_head

# agent_poll_jobs is deliberately not RLS-scoped (operational metadata only), so
# any authenticated connection to the test database works — same rationale as
# test_run_lock.py's advisory-lock tests.
PG_URL = os.getenv("RLS_TEST_ADMIN_URL", "")

pytestmark = pytest.mark.skipif(not PG_URL, reason="RLS_TEST_ADMIN_URL is required")


@pytest.fixture(autouse=True)
def _job_queue_db(monkeypatch):
    monkeypatch.setattr(settings, "database_url", PG_URL)
    monkeypatch.setattr(settings, "run_registry_backend", "postgres")
    upgrade_to_head()
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agent_poll_jobs")


def test_require_job_queue_available_needs_database_url(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "job_queue_enabled", True)
    with pytest.raises(RuntimeError):
        require_job_queue_available()


def test_enqueue_then_claim_returns_the_job():
    enqueued = enqueue_job("agent-a", "msg-1")
    assert enqueued["status"] == "pending"

    claimed = claim_job("worker-1")
    assert claimed["id"] == enqueued["id"]
    assert claimed["status"] == "processing"
    assert claimed["claimed_by"] == "worker-1"


def test_claim_prefers_the_least_busy_instance_for_fairness():
    """S-scale-3: once an instance has a job in flight, a newer job from an
    idle instance jumps ahead of that instance's older backlog — plain FIFO
    would starve agent-b behind agent-a's head start."""
    a1 = enqueue_job("agent-a", "a-msg-1")
    a2 = enqueue_job("agent-a", "a-msg-2")
    b1 = enqueue_job("agent-b", "b-msg-1")

    first = claim_job("worker-1")
    assert first["id"] == a1["id"]  # both instances idle: oldest overall wins

    second = claim_job("worker-2")
    assert second["id"] == b1["id"]  # agent-a now has 1 in flight; agent-b is idle

    third = claim_job("worker-3")
    assert third["id"] == a2["id"]  # queue drains once nobody is idle anymore


def test_duplicate_enqueue_is_a_noop_while_active():
    first = enqueue_job("agent-a", "msg-dup")
    second = enqueue_job("agent-a", "msg-dup")

    assert first is not None
    assert second is None
    assert count_jobs(status="pending", instance_id="agent-a") == 1


def test_claim_returns_none_when_queue_empty():
    assert claim_job("worker-1") is None


def test_mark_done_removes_job_from_pending_pool():
    job = enqueue_job("agent-a", "msg-2")
    claim_job("worker-1")
    mark_job_done(job["id"])

    assert count_jobs(status="done") == 1
    assert claim_job("worker-2") is None


def test_concurrent_claims_never_return_the_same_job():
    for i in range(10):
        enqueue_job("agent-a", f"msg-race-{i}")

    claimed_ids: list[int] = []
    lock = threading.Lock()

    def attempt(worker_id: str) -> None:
        job = claim_job(worker_id)
        if job:
            with lock:
                claimed_ids.append(job["id"])

    threads = [threading.Thread(target=attempt, args=(f"worker-{i}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed_ids) == 10
    assert len(set(claimed_ids)) == 10  # no two workers ever claimed the same job


def test_requeue_stale_jobs_recovers_an_orphaned_claim():
    job = enqueue_job("agent-a", "msg-stale")
    claim_job("worker-dead")
    _age_claim(job["id"], hours=1)

    requeued = requeue_stale_jobs(stale_seconds=60, max_attempts=5)

    assert requeued == 1
    assert count_jobs(status="pending") == 1


def test_requeue_stale_jobs_abandons_after_max_attempts():
    job = enqueue_job("agent-a", "msg-poison")
    claim_job("worker-dead")
    _age_claim(job["id"], hours=1, attempts=4)

    requeue_stale_jobs(stale_seconds=60, max_attempts=5)

    assert count_jobs(status="abandoned") == 1


def _age_claim(job_id: int, hours: int, attempts: int | None = None) -> None:
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            if attempts is None:
                cur.execute(
                    "UPDATE agent_poll_jobs SET claimed_at = NOW() - make_interval(hours => %s) WHERE id = %s",
                    (hours, job_id),
                )
            else:
                cur.execute(
                    "UPDATE agent_poll_jobs SET claimed_at = NOW() - make_interval(hours => %s), "
                    "attempts = %s WHERE id = %s",
                    (hours, attempts, job_id),
                )
