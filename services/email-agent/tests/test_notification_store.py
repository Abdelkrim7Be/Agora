from __future__ import annotations

import pytest

from src.config import settings
from src.notification_store import (
    create_notification,
    delete_notification,
    erase_notifications_for_subject,
    list_notifications,
    mark_all_read,
    mark_read,
    prune_notifications,
    unread_count,
)


@pytest.fixture(autouse=True)
def _json_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "run_registry_backend", "json")
    monkeypatch.setattr(settings, "notification_store_path", str(tmp_path / "notifications.json"))


def test_create_and_list():
    create_notification(notification_type="drafts_pending", title="Draft ready", user_id="u1", agent_instance_id="i1")
    create_notification(notification_type="sync_failed", title="Sync failed", user_id="u1", agent_instance_id="i1")

    rows = list_notifications(user_id="u1", agent_instance_id="i1")
    assert len(rows) == 2
    assert rows[0]["title"] == "Sync failed"  # newest first


def test_dedupe_increments_instead_of_inserting():
    create_notification(
        notification_type="sync_failed", title="Sync failed", user_id="u1", agent_instance_id="i1",
        dedupe_key="sync:i1",
    )
    create_notification(
        notification_type="sync_failed", title="Sync failed again", user_id="u1", agent_instance_id="i1",
        dedupe_key="sync:i1",
    )

    rows = list_notifications(user_id="u1", agent_instance_id="i1")
    assert len(rows) == 1
    assert rows[0]["occurrence_count"] == 2
    assert rows[0]["title"] == "Sync failed again"


def test_dedupe_does_not_collapse_after_read():
    row = create_notification(
        notification_type="sync_failed", title="Sync failed", user_id="u1", agent_instance_id="i1",
        dedupe_key="sync:i1",
    )
    mark_read(row["id"])
    create_notification(
        notification_type="sync_failed", title="Sync failed again", user_id="u1", agent_instance_id="i1",
        dedupe_key="sync:i1",
    )

    rows = list_notifications(user_id="u1", agent_instance_id="i1")
    assert len(rows) == 2


def test_unread_count():
    row = create_notification(notification_type="drafts_pending", title="A", user_id="u1", agent_instance_id="i1")
    create_notification(notification_type="drafts_pending", title="B", user_id="u1", agent_instance_id="i1")
    assert unread_count(user_id="u1", agent_instance_id="i1") == 2

    mark_read(row["id"])
    assert unread_count(user_id="u1", agent_instance_id="i1") == 1


def test_mark_read():
    row = create_notification(notification_type="drafts_pending", title="A", user_id="u1", agent_instance_id="i1")
    updated = mark_read(row["id"])
    assert updated["read_at"] is not None


def test_mark_all_read_scoped_to_instance():
    create_notification(notification_type="drafts_pending", title="A", user_id="u1", agent_instance_id="i1")
    create_notification(notification_type="drafts_pending", title="B", user_id="u1", agent_instance_id="i2")

    marked = mark_all_read(user_id="u1", agent_instance_id="i1")

    assert marked == 1
    assert unread_count(user_id="u1", agent_instance_id="i1") == 0
    assert unread_count(user_id="u1", agent_instance_id="i2") == 1


def test_list_limit_clamped():
    for i in range(5):
        create_notification(notification_type="drafts_pending", title=f"n{i}", user_id="u1", agent_instance_id="i1")

    rows = list_notifications(user_id="u1", agent_instance_id="i1", limit=2)
    assert len(rows) == 2

    # limit is clamped to [1, 200], never raises for an out-of-range request.
    rows_over = list_notifications(user_id="u1", agent_instance_id="i1", limit=10_000)
    assert len(rows_over) == 5


def test_tenant_isolation():
    create_notification(notification_type="drafts_pending", title="A", user_id="u1", agent_instance_id="i1")
    create_notification(notification_type="drafts_pending", title="B", user_id="u2", agent_instance_id="i1")

    rows = list_notifications(user_id="u1", agent_instance_id="i1")
    assert len(rows) == 1
    assert rows[0]["title"] == "A"


def test_prune_by_age():
    import src.notification_store as store

    row = create_notification(notification_type="drafts_pending", title="Old", user_id="u1", agent_instance_id="i1")

    # Directly age the row's created_at past the cutoff.
    data = store._json_read()
    for entry in data["notifications"]:
        if entry["id"] == row["id"]:
            entry["created_at"] = "2000-01-01T00:00:00+00:00"
    store._json_write(data)

    removed = prune_notifications(older_than_days=30)
    assert removed == 1
    assert list_notifications(user_id="u1", agent_instance_id="i1") == []


def test_gdpr_erasure_removes_notifications():
    create_notification(notification_type="setup_completed", title="Done", user_id="alice@example.com", agent_instance_id="i1")

    removed = erase_notifications_for_subject("alice@example.com", "i1")

    assert removed == 1
    assert list_notifications(user_id="alice@example.com", agent_instance_id="i1") == []


async def test_emission_failure_does_not_break_caller(monkeypatch):
    """B.3 contract: every emission point wraps create_notification in try/except
    so a notification-store outage never breaks the calling flow. instance_setup's
    finalize step is one such emission point."""
    import src.instance_setup as instance_setup

    def _boom(**kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("src.notification_store.create_notification", _boom)

    context = instance_setup.SetupContext(user_id="u1", agent_instance_id="i1")
    detail = await instance_setup._step_finalize(context)  # must not raise
    assert detail == {}


def test_delete_notification():
    row = create_notification(notification_type="drafts_pending", title="A", user_id="u1", agent_instance_id="i1")
    delete_notification(row["id"])
    assert list_notifications(user_id="u1", agent_instance_id="i1") == []


# --- Postgres backend (RLS) ---
# Regression coverage for a real bug: the pg helpers here once called
# tenant_connection(user_id=..., agent_instance_id=...) — that function takes
# no such kwargs (they went straight into psycopg.connect() and raised
# "invalid connection option"). Only caught by exercising the actual Postgres
# path, since the JSON-backend tests above monkeypatch database_url="".

import os

PG_URL = os.getenv("RLS_TEST_ADMIN_URL", "")

pg_only = pytest.mark.skipif(not PG_URL, reason="RLS_TEST_ADMIN_URL is required")


@pytest.fixture
def _pg_backend(monkeypatch):
    monkeypatch.setattr(settings, "database_url", PG_URL)
    monkeypatch.setattr(settings, "run_registry_backend", "postgres")
    from src.migrate import upgrade_to_head

    upgrade_to_head()
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM email_agent_notification")


@pg_only
def test_pg_create_list_unread_count_and_mark_read(_pg_backend):
    row = create_notification(
        notification_type="drafts_pending", title="PG note", user_id="pg-user", agent_instance_id="pg-instance"
    )
    assert unread_count(user_id="pg-user", agent_instance_id="pg-instance") == 1

    rows = list_notifications(user_id="pg-user", agent_instance_id="pg-instance")
    assert len(rows) == 1
    assert rows[0]["title"] == "PG note"

    mark_read(row["id"])
    assert unread_count(user_id="pg-user", agent_instance_id="pg-instance") == 0


@pg_only
def test_pg_mark_all_read_scoped_to_instance(_pg_backend):
    create_notification(notification_type="drafts_pending", title="A", user_id="pg-user", agent_instance_id="pg-i1")
    create_notification(notification_type="drafts_pending", title="B", user_id="pg-user", agent_instance_id="pg-i2")

    marked = mark_all_read(user_id="pg-user", agent_instance_id="pg-i1")

    assert marked == 1
    assert unread_count(user_id="pg-user", agent_instance_id="pg-i1") == 0
    assert unread_count(user_id="pg-user", agent_instance_id="pg-i2") == 1


@pg_only
def test_pg_erase_for_subject_removes_rows(_pg_backend):
    create_notification(
        notification_type="setup_completed", title="Done", user_id="owner@example.com", agent_instance_id="pg-instance"
    )

    removed = erase_notifications_for_subject("owner@example.com", "pg-instance")

    assert removed == 1
    assert list_notifications(user_id="owner@example.com", agent_instance_id="pg-instance") == []
