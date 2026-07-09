from __future__ import annotations

import logging

from src.config import SERVICE_ROOT, settings

logger = logging.getLogger(__name__)


def postgres_schema_selected() -> bool:
    """Whether any app-owned store runs on Postgres and therefore needs Alembic.

    The langgraph checkpoint/store tables are created by langgraph itself and are
    intentionally excluded here.
    """
    if not settings.database_url:
        return False
    backends = (
        settings.run_registry_backend,
        settings.cost_backend,
    )
    return any(backend.lower().strip() == "postgres" for backend in backends)


def upgrade_to_head() -> None:
    """Bring the app's Postgres schema up to head via Alembic.

    No-op unless a Postgres backend is selected (dev SQLite/json need nothing).
    Idempotent: Alembic no-ops when already at head, and Postgres runs DDL in a
    transaction guarded by the ``alembic_version`` lock, so a concurrent api +
    poller startup is safe. This replaces the hand-rolled ``setup_*`` DDL, which
    now defers to migrations.
    """
    if not postgres_schema_selected():
        return

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(SERVICE_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVICE_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    logger.info("Applying Alembic migrations to head")
    command.upgrade(cfg, "head")
