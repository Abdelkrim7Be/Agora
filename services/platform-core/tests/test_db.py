from __future__ import annotations

import pytest

from platform_core.db import TenantDatabase


class _Cursor:
    def __init__(self, row=None):
        self.row = row
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row=None):
        self.cursors = []
        self.row = row

    def cursor(self):
        cursor = _Cursor(self.row)
        self.cursors.append(cursor)
        return cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _db(*, database_url="postgresql://example", tenant_mode="multi", user="alice", instance="inst"):
    return TenantDatabase(
        database_url=lambda: database_url,
        tenant_mode=lambda: tenant_mode,
        normalize_user_id=lambda value: (value or "").strip().lower(),
        normalize_agent_instance_id=lambda value: (value or "").strip().lower(),
        current_user_id=lambda: user,
        current_agent_instance_id=lambda: instance,
    )


def test_binding_sets_both_rls_variables_from_the_ambient_tenant():
    connection = _Connection()

    _db().bind_tenant_context(connection)

    _, params = connection.cursors[0].executed[0]
    assert params == ("alice", "inst")


def test_explicit_identity_overrides_the_ambient_tenant():
    connection = _Connection()

    _db().bind_tenant_context(connection, user_id="  BOB  ", agent_instance_id="Other")

    _, params = connection.cursors[0].executed[0]
    assert params == ("bob", "other")


@pytest.mark.parametrize(
    "row",
    [(True, False), (False, True), (True, True), None],
    ids=["superuser", "bypassrls", "both", "role-missing"],
)
def test_multi_tenant_rejects_any_role_that_can_read_past_rls(row):
    connection = _Connection(row=row)

    with pytest.raises(RuntimeError, match="NOBYPASSRLS"):
        _db().validate_runtime_role(lambda: connection)


def test_an_ordinary_role_is_accepted():
    _db().validate_runtime_role(lambda: _Connection(row=(False, False)))


@pytest.mark.parametrize(
    "kwargs",
    [{"tenant_mode": "single"}, {"database_url": ""}],
    ids=["single-tenant", "no-database"],
)
def test_validation_is_skipped_when_rls_does_not_apply(kwargs):
    def _explode():
        raise AssertionError("should not have connected")

    _db(**kwargs).validate_runtime_role(_explode)
