from __future__ import annotations

from typing import Any

from src.config import settings

# agent_poll_jobs is deliberately NOT row-level-secured (see migration 0010) — it
# holds only instance_id + Gmail message_id, no email content. claim_job() must
# see pending jobs across every tenant in one atomic query, so this module talks
# to Postgres directly instead of through src.postgres.tenant_connection().


def require_job_queue_available() -> None:
    if settings.job_queue_enabled and not settings.database_url:
        raise RuntimeError("DATABASE_URL is required when AGENT_JOB_QUEUE_ENABLED=true")


def _connect():
    import psycopg

    return psycopg.connect(settings.database_url)


def _row(row: dict[str, Any]) -> dict:
    return {
        "id": row["id"],
        "instance_id": row["instance_id"],
        "message_id": row["message_id"],
        "status": row["status"],
        "attempts": row["attempts"],
        "claimed_by": row["claimed_by"],
        "claimed_at": row["claimed_at"].isoformat() if row["claimed_at"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def enqueue_job(instance_id: str, message_id: str) -> dict | None:
    """Enqueue one pending job. Returns None if an active job for this
    instance+message already exists (producer re-detected the same unread
    email before a worker claimed/finished it — idempotent, not an error)."""
    from psycopg.rows import dict_row

    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO agent_poll_jobs (instance_id, message_id)
                VALUES (%(instance_id)s, %(message_id)s)
                ON CONFLICT DO NOTHING
                RETURNING id, instance_id, message_id, status, attempts,
                    claimed_by, claimed_at, created_at, updated_at
                """,
                {"instance_id": instance_id, "message_id": message_id},
            )
            row = cur.fetchone()
            return _row(row) if row else None


def claim_job(worker_id: str) -> dict | None:
    """Atomically claim the oldest pending job across every instance.

    SELECT ... FOR UPDATE SKIP LOCKED lets concurrent workers each grab a
    different row in one round trip instead of blocking on each other.

    Fairness (S-scale-3): ordering prefers the instance with the fewest jobs
    currently 'processing', tie-broken by age. A mailbox with a large backlog
    still gets served (nothing here blocks it), but once it has jobs in flight
    a newer job from an idle instance jumps ahead of that mailbox's older
    backlog — one noisy tenant can't hold every worker.
    """
    from psycopg.rows import dict_row

    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                WITH next AS (
                    SELECT j.id FROM agent_poll_jobs j
                    WHERE j.status = 'pending'
                    ORDER BY
                        (SELECT COUNT(*) FROM agent_poll_jobs p
                         WHERE p.instance_id = j.instance_id AND p.status = 'processing') ASC,
                        j.created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE agent_poll_jobs
                SET status = 'processing', claimed_by = %(worker_id)s,
                    claimed_at = NOW(), updated_at = NOW()
                FROM next
                WHERE agent_poll_jobs.id = next.id
                RETURNING agent_poll_jobs.id, agent_poll_jobs.instance_id,
                    agent_poll_jobs.message_id, agent_poll_jobs.status,
                    agent_poll_jobs.attempts, agent_poll_jobs.claimed_by,
                    agent_poll_jobs.claimed_at, agent_poll_jobs.created_at,
                    agent_poll_jobs.updated_at
                """,
                {"worker_id": worker_id},
            )
            row = cur.fetchone()
            return _row(row) if row else None


def mark_job_done(job_id: int) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE agent_poll_jobs SET status = 'done', updated_at = NOW() "
                "WHERE id = %(id)s",
                {"id": job_id},
            )


def requeue_stale_jobs(
    stale_seconds: float | None = None,
    max_attempts: int | None = None,
) -> int:
    """Recover jobs orphaned by a worker crash between claim and done.

    A stale 'processing' job goes back to 'pending' with attempts+1; past
    max_attempts it is marked 'abandoned' instead so a wedged job (e.g. a
    poison message) doesn't loop forever.
    """
    stale_seconds = settings.job_queue_stale_seconds if stale_seconds is None else stale_seconds
    max_attempts = settings.job_queue_max_attempts if max_attempts is None else max_attempts
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE agent_poll_jobs
                SET status = CASE WHEN attempts + 1 >= %(max_attempts)s
                        THEN 'abandoned' ELSE 'pending' END,
                    attempts = attempts + 1,
                    claimed_by = NULL,
                    claimed_at = NULL,
                    updated_at = NOW()
                WHERE status = 'processing'
                  AND claimed_at < NOW() - make_interval(secs => %(stale_seconds)s)
                """,
                {"stale_seconds": stale_seconds, "max_attempts": max_attempts},
            )
            return cur.rowcount


def count_jobs(status: str | None = None, instance_id: str | None = None) -> int:
    from psycopg.rows import dict_row

    clauses = []
    params: dict[str, Any] = {}
    if status is not None:
        clauses.append("status = %(status)s")
        params["status"] = status
    if instance_id is not None:
        clauses.append("instance_id = %(instance_id)s")
        params["instance_id"] = instance_id
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM agent_poll_jobs {where}", params)
            return int(cur.fetchone()["n"])
