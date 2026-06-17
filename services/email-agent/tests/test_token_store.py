from __future__ import annotations

from pathlib import Path

from src.config import SERVICE_ROOT, settings
from src.tenant import current_user_id, normalize_user_id, resolve_user_id, user_context
from src.token_store import prepared_token_file, token_file_for_user


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


def test_prepared_token_file_passthrough_without_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "tokens"))

    with prepared_token_file("alice@example.com") as token_path:
        assert token_path.endswith("alice@example.com.json")


def test_prepared_token_file_encrypts_at_rest(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "token_encryption_key", "unit-test-secret")
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "tokens"))

    # First use: the Google client would write the plaintext token here.
    with prepared_token_file("alice@example.com") as token_path:
        Path(token_path).write_text('{"token": "secret-oauth"}')

    plaintext = tmp_path / "tokens" / "alice@example.com.json"
    encrypted = tmp_path / "tokens" / "alice@example.com.json.enc"
    # Plaintext is removed; only the encrypted blob remains, and it isn't readable.
    assert not plaintext.exists()
    assert encrypted.is_file()
    assert b"secret-oauth" not in encrypted.read_bytes()

    # Second use: the blob is transparently decrypted back to the plaintext path.
    with prepared_token_file("alice@example.com") as token_path:
        assert Path(token_path).read_text() == '{"token": "secret-oauth"}'


def test_resolve_user_id_maps_external_identity(monkeypatch) -> None:
    monkeypatch.setattr(settings, "user_map", {"alice@gmail.com": "alice"})

    assert resolve_user_id("Alice@Gmail.com") == "alice"
    assert resolve_user_id("bob@gmail.com") == "bob@gmail.com"
