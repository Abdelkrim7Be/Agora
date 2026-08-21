from __future__ import annotations

import os

import pytest

from src.config import settings
from src.migrate import upgrade_to_head
from src.postgres import tenant_connection
from src.tenant import agent_instance_context, user_context

ADMIN_URL = os.getenv("RLS_TEST_ADMIN_URL", "")
APP_URL = os.getenv("RLS_TEST_APP_URL", "")

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL,
    reason="RLS_TEST_ADMIN_URL and RLS_TEST_APP_URL are required",
)


def test_forced_rls_blocks_cross_tenant_reads_and_writes(monkeypatch) -> None:
    import psycopg

    with psycopg.connect(ADMIN_URL, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles WHERE rolname = 'agora_email_agent'
                    ) THEN
                        CREATE ROLE agora_email_agent LOGIN PASSWORD 'agent-test-password'
                            NOSUPERUSER NOBYPASSRLS;
                    END IF;
                END
                $$
                """
            )
            cursor.execute(
                """
                ALTER ROLE agora_email_agent PASSWORD 'agent-test-password'
                    NOSUPERUSER NOBYPASSRLS
                """
            )
            cursor.execute("GRANT CONNECT ON DATABASE agora_ci TO agora_email_agent")
            cursor.execute("GRANT USAGE ON SCHEMA public TO agora_email_agent")

    monkeypatch.setattr(settings, "database_url", APP_URL)
    monkeypatch.setattr(settings, "migration_database_url", ADMIN_URL)
    monkeypatch.setattr(settings, "run_migrations", True)
    monkeypatch.setattr(settings, "run_registry_backend", "postgres")
    upgrade_to_head()

    with user_context("alice"):
        with agent_instance_context("finance"):
            with tenant_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO agent_runs (
                            run_id, user_id, agent_instance_id, status, priority,
                            workflow_route_to, created_at, updated_at
                        ) VALUES (
                            'alice-run', 'alice', 'finance', 'completed', 'normal',
                            '[]'::jsonb, NOW(), NOW()
                        )
                        """
                    )
                    cursor.execute(
                        """
                        INSERT INTO email_agent_instance_config (
                            agent_instance_id, config_kind, content
                        ) VALUES ('finance', 'policy', 'alice-only')
                        """
                    )

    # A run belongs to the mailbox, not to whoever happened to trigger it: two
    # people validating the same shared instance is the normal case, so bob sees
    # alice's run on the instance they both work.
    with user_context("bob"):
        with agent_instance_context("finance"):
            with tenant_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT run_id FROM agent_runs")
                    assert cursor.fetchall() == [("alice-run",)]

    # The instance is still the wall. Another instance cannot read that run, and
    # cannot write one into finance either.
    with user_context("bob"):
        with agent_instance_context("legal"):
            with tenant_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT run_id FROM agent_runs")
                    assert cursor.fetchall() == []
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        cursor.execute(
                            """
                            INSERT INTO agent_runs (
                                run_id, user_id, agent_instance_id, status, priority,
                                workflow_route_to, created_at, updated_at
                            ) VALUES (
                                'forged-run', 'bob', 'finance', 'completed', 'normal',
                                '[]'::jsonb, NOW(), NOW()
                            )
                            """
                        )
                    connection.rollback()

    with user_context("alice"):
        with agent_instance_context("legal"):
            with tenant_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT content FROM email_agent_instance_config")
                    assert cursor.fetchall() == []
