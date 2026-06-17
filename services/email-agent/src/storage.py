from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

from src.config import settings


@dataclass
class GraphStorage:
    backend: str
    checkpointer: object
    store: object


def selected_storage_backend() -> str:
    backend = settings.storage_backend.lower().strip()
    if backend not in {"sqlite", "postgres"}:
        raise RuntimeError(f"Unsupported AGENT_STORAGE_BACKEND: {settings.storage_backend}")
    if backend == "postgres" and not settings.database_url:
        raise RuntimeError("DATABASE_URL is required when AGENT_STORAGE_BACKEND=postgres")
    return backend


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
async def open_graph_storage() -> AsyncIterator[GraphStorage]:
    backend = selected_storage_backend()
    if backend == "sqlite":
        async with AsyncSqliteSaver.from_conn_string(settings.checkpoints_db) as checkpointer:
            async with AsyncSqliteStore.from_conn_string(settings.store_db) as mem_store:
                await checkpointer.setup()
                await mem_store.setup()
                yield GraphStorage(backend=backend, checkpointer=checkpointer, store=mem_store)
        return

    AsyncPostgresSaver, AsyncPostgresStore = _postgres_classes()
    async with AsyncPostgresSaver.from_conn_string(settings.database_url) as checkpointer:
        async with AsyncPostgresStore.from_conn_string(settings.database_url) as mem_store:
            await checkpointer.setup()
            await mem_store.setup()
            yield GraphStorage(backend=backend, checkpointer=checkpointer, store=mem_store)
