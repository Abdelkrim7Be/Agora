from __future__ import annotations

from pathlib import Path

from src import cost_tracker, gmail_sync, instance_config, migrate, run_registry, sync_status
from src.config import settings

SERVICE_ROOT = Path(__file__).resolve().parent.parent


def test_alembic_scaffold_and_scripts_exist() -> None:
    assert (SERVICE_ROOT / "alembic.ini").is_file()
    assert (SERVICE_ROOT / "migrations" / "env.py").is_file()
    assert (SERVICE_ROOT / "migrations" / "versions" / "0001_baseline.py").is_file()
    assert (SERVICE_ROOT / "migrations" / "versions" / "0002_roles_directory.py").is_file()
    assert (SERVICE_ROOT / "migrations" / "versions" / "0003_contacts.py").is_file()
    assert (SERVICE_ROOT / "migrations" / "versions" / "0004_segments.py").is_file()
    assert (SERVICE_ROOT / "migrations" / "versions" / "0007_trace_retention.py").is_file()
    assert (SERVICE_ROOT / "migrations" / "versions" / "0008_dlq.py").is_file()
    assert (SERVICE_ROOT / "scripts" / "backup.sh").is_file()
    assert (SERVICE_ROOT / "scripts" / "restore.sh").is_file()
    assert (SERVICE_ROOT / "docs" / "backup-restore.md").is_file()


def test_baseline_migration_covers_current_app_tables() -> None:
    text = (SERVICE_ROOT / "migrations" / "versions" / "0001_baseline.py").read_text(encoding="utf-8")

    for marker in (
        '"agent_runs"',
        '"gmail_sync_state"',
        '"email_agent_sync"',
        '"llm_costs"',
        '"email_agent_instance_config"',
        'workflow_route_to',
        "'[]'::jsonb",
        'agent_runs_user_instance_status_updated_idx',
        'llm_costs_user_instance_timestamp_idx',
    ):
        assert marker in text


def test_roles_migration_covers_directory_table() -> None:
    text = (SERVICE_ROOT / "migrations" / "versions" / "0002_roles_directory.py").read_text(encoding="utf-8")
    for marker in (
        '"email_agent_roles"',
        '"emails"',
        'email_agent_roles_pkey',
        'email_agent_roles_instance_dept_idx',
        "'[]'::jsonb",
    ):
        assert marker in text




def test_trace_migration_covers_langfuse_style_rows() -> None:
    text = (SERVICE_ROOT / "migrations" / "versions" / "0007_trace_retention.py").read_text(encoding="utf-8")
    for marker in (
        '"llm_traces"',
        '"started_at"',
        '"finished_at"',
        'llm_traces_event_id_key',
        'llm_traces_run_timestamp_idx',
    ):
        assert marker in text


def test_contacts_and_segments_migrations_cover_directory_tables() -> None:
    contacts_text = (SERVICE_ROOT / "migrations" / "versions" / "0003_contacts.py").read_text(encoding="utf-8")
    for marker in (
        '"email_agent_contacts"',
        '"audience"',
        'email_agent_contacts_pkey',
        'email_agent_contacts_instance_audience_idx',
        "'{}'::jsonb",
        "'[]'::jsonb",
    ):
        assert marker in contacts_text

    segments_text = (SERVICE_ROOT / "migrations" / "versions" / "0004_segments.py").read_text(encoding="utf-8")
    for marker in (
        '"email_agent_segments"',
        '"segment_id"',
        'email_agent_segments_pkey',
        'email_agent_segments_instance_idx',
        "'{}'::jsonb",
        "'[]'::jsonb",
    ):
        assert marker in segments_text


def test_postgres_setup_functions_defer_to_alembic(monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", "postgresql://example")
    monkeypatch.setattr(settings, "run_registry_backend", "postgres")
    monkeypatch.setattr(settings, "cost_backend", "postgres")
    monkeypatch.setattr(run_registry, "_connect", lambda: (_ for _ in ()).throw(AssertionError("run_registry should not connect")))
    monkeypatch.setattr(gmail_sync, "_connect", lambda: (_ for _ in ()).throw(AssertionError("gmail_sync should not connect")))
    monkeypatch.setattr(sync_status, "_connect", lambda: (_ for _ in ()).throw(AssertionError("sync_status should not connect")))
    monkeypatch.setattr(cost_tracker, "_connect", lambda: (_ for _ in ()).throw(AssertionError("cost_tracker should not connect")))
    monkeypatch.setattr(instance_config, "_connect", lambda: (_ for _ in ()).throw(AssertionError("instance_config should not connect")))
    monkeypatch.setattr(instance_config, "_schema_ready", False)

    run_registry.setup_run_registry()
    gmail_sync.setup_gmail_sync()
    sync_status.setup_sync_status()
    cost_tracker.setup_cost_tracker()
    instance_config.setup_instance_config()

    assert instance_config._schema_ready is True


def test_backup_restore_docs_capture_langgraph_boundary() -> None:
    text = (SERVICE_ROOT / "docs" / "backup-restore.md").read_text(encoding="utf-8")
    assert "LangGraph checkpoint and store tables remain outside Alembic" in text
    assert "pg_dump" in text
    assert "pg_restore" in text


def test_upgrade_to_head_noop_without_postgres(monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "run_registry_backend", "json")
    monkeypatch.setattr(settings, "cost_backend", "json")

    import alembic.command as alembic_command

    def _fail_upgrade(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("alembic upgrade must not run without a Postgres backend")

    monkeypatch.setattr(alembic_command, "upgrade", _fail_upgrade)
    # No Postgres selected -> returns before touching alembic.
    migrate.upgrade_to_head()


def test_upgrade_to_head_runs_alembic_on_postgres(monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", "postgresql://example/db")
    monkeypatch.setattr(settings, "run_registry_backend", "postgres")
    monkeypatch.setattr(settings, "cost_backend", "postgres")

    captured = {}
    import alembic.command as alembic_command

    def _fake_upgrade(cfg, revision):
        captured["revision"] = revision
        captured["url"] = cfg.get_main_option("sqlalchemy.url")

    monkeypatch.setattr(alembic_command, "upgrade", _fake_upgrade)
    migrate.upgrade_to_head()
    assert captured["revision"] == "head"
    assert captured["url"] == "postgresql://example/db"


def test_entrypoints_apply_migrations_before_setup() -> None:
    # Regression guard: both the API lifespan and the poller loop must run
    # migrations before touching the (now DDL-free) setup_* functions, or a
    # fresh Postgres deployment would start with no tables.
    api_src = (SERVICE_ROOT / "src" / "api.py").read_text(encoding="utf-8")
    poller_src = (SERVICE_ROOT / "src" / "poller.py").read_text(encoding="utf-8")
    for src in (api_src, poller_src):
        assert "upgrade_to_head()" in src
        assert src.index("upgrade_to_head()") < src.index("setup_run_registry()")



def test_dlq_migration_covers_dead_letter_table() -> None:
    text = (SERVICE_ROOT / "migrations" / "versions" / "0008_dlq.py").read_text(encoding="utf-8")
    for marker in (
        '"email_agent_dlq"',
        '"requeue_token"',
        '"payload"',
        'email_agent_dlq_instance_status_timestamp_idx',
    ):
        assert marker in text
