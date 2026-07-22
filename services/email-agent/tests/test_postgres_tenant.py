from __future__ import annotations

import pytest

from src.config import settings
from src.postgres import bind_tenant_context, validate_runtime_role
from src.tenant import agent_instance_context, user_context


class _Cursor:
    def __init__(self, row=(False, False)):
        self.calls = []
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.calls.append((query, params))

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row=(False, False)):
        self.cursor_value = _Cursor(row)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value


def test_bind_tenant_context_sets_normalized_user_and_instance() -> None:
    connection = _Connection()

    with user_context("alice+finance@example.com"):
        with agent_instance_context("Finance Inbox"):
            assert bind_tenant_context(connection) is connection

    query, params = connection.cursor_value.calls[0]
    assert "set_config('agora.user_id'" in query
    assert "set_config('agora.agent_instance_id'" in query
    assert params == ("alice_finance@example.com", "Finance_Inbox")


def test_multi_tenant_runtime_rejects_bypassrls_role(monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", "postgresql://example")
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    connection = _Connection(row=(False, True))
    monkeypatch.setattr("src.postgres.tenant_connection", lambda: connection)

    with pytest.raises(RuntimeError, match="NOBYPASSRLS"):
        validate_runtime_role()


def test_single_tenant_runtime_keeps_local_backends_compatible(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tenant_mode", "single")
    monkeypatch.setattr(
        "src.postgres.tenant_connection",
        lambda: (_ for _ in ()).throw(AssertionError("must not connect")),
    )

    validate_runtime_role()
