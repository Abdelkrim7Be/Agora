from __future__ import annotations

from src.config import SERVICE_ROOT, settings
from src.tenant import current_user_id, normalize_user_id, user_context
from src.token_store import token_file_for_user


def test_default_single_user_keeps_legacy_token_path(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tenant_mode", "single")
    monkeypatch.setattr(settings, "default_user_id", "default")
    monkeypatch.setattr(settings, "gmail_token_path", "token.json")

    assert token_file_for_user() == SERVICE_ROOT / "token.json"


def test_explicit_user_gets_isolated_token_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "gmail_tokens.json"))

    path = token_file_for_user("alice@example.com")

    assert path == tmp_path / "gmail_tokens" / "alice@example.com.json"
    assert path.parent.is_dir()


def test_user_id_is_sanitized_for_file_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "tokens"))

    assert token_file_for_user("../bob/team").name == "bob_team.json"


def test_user_context_sets_current_user() -> None:
    with user_context("alice@example.com"):
        assert current_user_id() == "alice@example.com"

    assert current_user_id() == normalize_user_id(settings.default_user_id)
