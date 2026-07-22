from __future__ import annotations

import fcntl
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.config import settings


def _lock_key(agent_instance_id: str, message_id: str) -> str:
    return f"{agent_instance_id}:{message_id}"


def _pg_advisory_lock_id(key: str) -> int:
    # pg_advisory_lock takes a signed bigint; fold a stable hash into that range.
    digest = hashlib.sha256(key.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


@contextmanager
def _postgres_claim(key: str) -> Iterator[bool]:
    from src.postgres import tenant_connection

    lock_id = _pg_advisory_lock_id(key)
    connection = tenant_connection(autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_id,))
            acquired = bool(cursor.fetchone()[0])
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
    finally:
        connection.close()


@contextmanager
def _file_claim(key: str) -> Iterator[bool]:
    lock_dir = Path(settings.token_work_dir) / "run-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".lock")
    handle = open(lock_path, "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


@contextmanager
def try_claim_message(agent_instance_id: str, message_id: str) -> Iterator[bool]:
    """Mutual exclusion so a webhook push and the polling fallback never process
    the same Gmail message at the same time.

    Yields True if this call owns the message for the duration of the ``with``
    block. Yields False when another worker already holds it right now — the
    caller should skip; the owner's run is visible on the next status check.
    Backed by a Postgres advisory lock in multi-process/multi-host deployments,
    or a local file lock when there is no Postgres backend (single-host dev).
    """
    key = _lock_key(agent_instance_id, message_id)
    claim = _postgres_claim if settings.database_url else _file_claim
    with claim(key) as acquired:
        yield acquired
