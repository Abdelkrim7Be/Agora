from __future__ import annotations

from pathlib import Path

from src.config import SERVICE_ROOT, settings
from src.tenant import (
    agent_instance_context,
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
    resolve_user_id,
    user_context,
)
from src.token_store import has_stored_token, prepared_token_file, token_file_for_user


def test_default_single_user_keeps_legacy_token_path(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tenant_mode", "single")
    monkeypatch.setattr(settings, "default_user_id", "default")
    monkeypatch.setattr(settings, "gmail_token_path", "token.json")

    assert token_file_for_user() == SERVICE_ROOT / "token.json"


def test_delegated_users_share_instance_token_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "default-token.json"))

    alice = token_file_for_user("alice@example.com")
    viewer = token_file_for_user("viewer@example.com")

    assert alice == tmp_path / "default-token.json"
    assert viewer == alice


def test_instance_id_is_sanitized_for_file_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "tokens"))

    assert token_file_for_user("../bob/team", "../ceo/mailbox").name == "instance__ceo_mailbox.json"


def test_has_stored_token_detects_plaintext_and_encrypted_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "tokens"))
    instance_id = "ceo-email-agent"
    target = token_file_for_user(agent_instance_id=instance_id)

    assert has_stored_token(instance_id) is False
    target.write_text("{}")
    assert has_stored_token(instance_id) is True
    target.unlink()
    target.with_name(target.name + ".enc").write_bytes(b"encrypted")
    assert has_stored_token(instance_id) is True


def test_user_context_sets_current_user() -> None:
    with user_context("alice@example.com"):
        assert current_user_id() == "alice@example.com"

    assert current_user_id() == normalize_user_id(settings.default_user_id)


def test_prepared_token_file_passthrough_without_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))

    with prepared_token_file("alice@example.com") as token_path:
        assert token_path.endswith("token.json")


def test_prepared_token_file_encrypts_at_rest(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "token_encryption_key", "unit-test-secret")
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))

    # First use: the Google client would write the plaintext token here.
    with prepared_token_file("alice@example.com") as token_path:
        Path(token_path).write_text('{"token": "secret-oauth"}')

    plaintext = tmp_path / "token.json"
    encrypted = tmp_path / "token.json.enc"
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


def test_agent_instance_context_sets_current_instance() -> None:
    with agent_instance_context("ceo-email-agent"):
        assert current_agent_instance_id() == "ceo-email-agent"

    assert current_agent_instance_id() == normalize_agent_instance_id(settings.default_agent_instance_id)


def test_non_default_instance_gets_isolated_token_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "tenant_mode", "single")
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "gmail_tokens.json"))

    path = token_file_for_user(agent_instance_id="ceo-email-agent")

    assert path == tmp_path / "gmail_tokens" / "instance__ceo-email-agent.json"
