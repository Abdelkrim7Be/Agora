from __future__ import annotations

import pytest

import src.roles as roles
from src.roles import (
    DEFAULT_ROLES_PATH,
    ResolvedRole,
    RoleConflictError,
    RoleNotFoundError,
    create_role,
    delete_role,
    dump_roles,
    list_roles,
    load_roles,
    normalize_role_key,
    resolve_role,
    update_role,
)


@pytest.fixture(autouse=True)
def _local_backend(monkeypatch):
    monkeypatch.setattr(roles.settings, "database_url", "")


def test_seed_directory_matches_current_demo_routes() -> None:
    hr = resolve_role("hr")
    rh = resolve_role("rh")
    finance = resolve_role("finance")

    assert hr is not None
    assert rh is not None
    assert finance is not None
    assert hr.primary_email == "amina.diallo@example.com"
    assert rh.primary_email == "amina.diallo@example.com"
    assert finance.primary_email == "julien.moreau@example.com"


def test_unknown_role_returns_none() -> None:
    assert resolve_role("unknown routing role") is None


def test_multi_email_role_keeps_full_list_and_primary(tmp_path, monkeypatch) -> None:
    path = tmp_path / "roles.yaml"
    path.write_text(
        "roles:\n"
        "  legal:\n"
        "    display_name: Legal\n"
        "    dept: Legal\n"
        "    emails:\n"
        "      - legal@example.com\n"
        "      - legal.backup@example.com\n"
    )
    monkeypatch.setattr(roles, "DEFAULT_ROLES_PATH", path)

    resolved = resolve_role(" legal ")

    assert resolved is not None
    assert resolved.role_key == "legal"
    assert resolved.primary_email == "legal@example.com"
    assert resolved.emails == ["legal@example.com", "legal.backup@example.com"]


def test_local_crud_round_trip(tmp_path, monkeypatch) -> None:
    path = tmp_path / "roles.yaml"
    path.write_text("roles: {}\n")
    monkeypatch.setattr(roles, "DEFAULT_ROLES_PATH", path)

    created = create_role("HR", "Human Resources", ["hr@example.com"], "HR")
    assert created.role_key == "hr"
    assert list_roles() == [
        ResolvedRole(role_key="hr", display_name="Human Resources", dept="HR", emails=["hr@example.com"])
    ]

    with pytest.raises(RoleConflictError):
        create_role("hr", "Duplicate", ["dup@example.com"], "HR")

    updated = update_role("hr", "HR Team", ["hr.primary@example.com", "hr.backup@example.com"], "People")
    assert updated.primary_email == "hr.primary@example.com"
    assert load_roles().roles["hr"].dept == "People"

    delete_role("hr")
    assert list_roles() == []
    with pytest.raises(RoleNotFoundError):
        delete_role("hr")


def test_dump_roles_normalizes_keys() -> None:
    cfg = load_roles(DEFAULT_ROLES_PATH)
    dumped = dump_roles(cfg)
    assert "roles:" in dumped
    assert normalize_role_key(" Human   Resources ") == "human resources"
