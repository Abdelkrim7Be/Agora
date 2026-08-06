from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from platform_core.storage import GraphStorage, open_graph_storage as _open, select_backend

from src.config import settings

__all__ = ["GraphStorage", "selected_storage_backend", "open_graph_storage"]


def selected_storage_backend() -> str:
    return select_backend(settings.storage_backend, settings.database_url)


@asynccontextmanager
async def open_graph_storage() -> AsyncIterator[GraphStorage]:
    async with _open(
        backend=lambda: settings.storage_backend,
        database_url=lambda: settings.database_url,
        checkpoints_db=lambda: settings.checkpoints_db,
        store_db=lambda: settings.store_db,
    ) as storage:
        yield storage
