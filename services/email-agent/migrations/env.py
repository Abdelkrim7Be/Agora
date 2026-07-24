from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would silence every app
    # logger already configured before migrations run — a real problem when
    # run_migrations=True fires at process startup (and it also breaks caplog
    # assertions in any test that migrates before the log-emitting test).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# No ORM metadata: the email-agent Postgres schema is migration-first.
# LangGraph checkpoint/store tables remain outside Alembic and are created by
# langgraph's own setup methods.
target_metadata = None


def _database_url() -> str:
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    # The app connects with psycopg (v3) directly everywhere else. SQLAlchemy's
    # engine_from_config defaults a bare "postgresql://" URL to the psycopg2
    # dialect, which isn't installed here — force the psycopg3 dialect instead.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
