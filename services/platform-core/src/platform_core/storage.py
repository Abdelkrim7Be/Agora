from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Callable

SUPPORTED_BACKENDS = frozenset({"sqlite", "postgres"})


@dataclass
class GraphStorage:
    backend: str
    checkpointer: object
    store: object


def select_backend(backend: str, database_url: str) -> str:
    """Resolve and validate the configured checkpoint/store backend."""
    resolved = backend.lower().strip()
    if resolved not in SUPPORTED_BACKENDS:
        raise RuntimeError(f"Unsupported AGENT_STORAGE_BACKEND: {backend}")
    if resolved == "postgres" and not database_url:
        raise RuntimeError("DATABASE_URL is required when AGENT_STORAGE_BACKEND=postgres")
    return resolved


# LangGraph is imported lazily so platform-core stays installable without it and
# a sqlite deployment never pays for the Postgres drivers.
def _sqlite_classes():
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.store.sqlite.aio import AsyncSqliteStore

    return AsyncSqliteSaver, AsyncSqliteStore


def _postgres_classes():
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.store.postgres.aio import AsyncPostgresStore
    except ImportError as exc:
        raise RuntimeError(
            "Postgres graph storage requires langgraph-checkpoint-postgres and psycopg."
        ) from exc
    return AsyncPostgresSaver, AsyncPostgresStore


@asynccontextmanager
async def open_graph_storage(
    *,
    backend: Callable[[], str],
    database_url: Callable[[], str],
    checkpoints_db: Callable[[], str],
    store_db: Callable[[], str],
) -> AsyncIterator[GraphStorage]:
    resolved = select_backend(backend(), database_url())
    if resolved == "sqlite":
        Saver, Store = _sqlite_classes()
        conn_args = (checkpoints_db(), store_db())
    else:
        Saver, Store = _postgres_classes()
        url = database_url()
        conn_args = (url, url)

    async with Saver.from_conn_string(conn_args[0]) as checkpointer:
        async with Store.from_conn_string(conn_args[1]) as mem_store:
            await checkpointer.setup()
            await mem_store.setup()
            yield GraphStorage(backend=resolved, checkpointer=checkpointer, store=mem_store)
