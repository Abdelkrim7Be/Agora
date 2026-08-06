from __future__ import annotations

import pytest

from platform_core.storage import select_backend


@pytest.mark.parametrize(
    "backend,expected",
    [("sqlite", "sqlite"), ("  SQLite ", "sqlite"), ("postgres", "postgres")],
)
def test_backend_names_are_normalized(backend, expected):
    assert select_backend(backend, "postgresql://example") == expected


def test_an_unknown_backend_is_refused():
    with pytest.raises(RuntimeError, match="AGENT_STORAGE_BACKEND"):
        select_backend("mysql", "")


def test_postgres_without_a_url_is_refused():
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        select_backend("postgres", "")


def test_sqlite_does_not_need_a_database_url():
    assert select_backend("sqlite", "") == "sqlite"
