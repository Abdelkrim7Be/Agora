from __future__ import annotations

import pytest

from src.config import settings
from src.storage import open_graph_storage, selected_storage_backend


def test_selected_storage_backend_defaults_to_sqlite(monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_backend", "sqlite")

    assert selected_storage_backend() == "sqlite"


def test_selected_storage_backend_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_backend", "bogus")

    with pytest.raises(RuntimeError, match="Unsupported AGENT_STORAGE_BACKEND"):
        selected_storage_backend()


def test_selected_storage_backend_requires_database_url(monkeypatch) -> None:
    monkeypatch.setattr(settings, "storage_backend", "postgres")
    monkeypatch.setattr(settings, "database_url", "")

    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        selected_storage_backend()


async def test_open_graph_storage_sqlite(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "storage_backend", "sqlite")
    monkeypatch.setattr(settings, "checkpoints_db", str(tmp_path / "checkpoints.sqlite"))
    monkeypatch.setattr(settings, "store_db", str(tmp_path / "store.sqlite"))

    async with open_graph_storage() as storage:
        assert storage.backend == "sqlite"
        assert storage.checkpointer is not None
        assert storage.store is not None
