from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
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
from src.token_store import (
    DEFAULT_KEY_ID,
    _decrypt_envelope,
    _derive_fernet,
    _encrypted_path,
    _load_envelope,
    delete_token,
    has_stored_token,
    prepared_token_file,
    token_file_for_user,
)


def _write_key_file(path: Path, payload) -> str:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


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
    monkeypatch.setattr(settings, "token_encryption_key_file", "")
    monkeypatch.setattr(settings, "token_encryption_required", False)
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))

    with prepared_token_file("alice@example.com") as token_path:
        assert token_path.endswith("token.json")


def test_prepared_token_file_encrypts_at_rest_as_envelope(monkeypatch, tmp_path) -> None:
    key_file = tmp_path / "token-key.json"
    monkeypatch.setattr(settings, "token_encryption_key_file", _write_key_file(key_file, {"active_key_id": "k1", "keys": {"k1": "unit-test-secret"}}))
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "token_encryption_required", True)
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))

    with prepared_token_file("alice@example.com") as token_path:
        Path(token_path).write_text('{"token": "secret-oauth"}', encoding="utf-8")

    plaintext = tmp_path / "token.json"
    encrypted = tmp_path / "token.json.enc"
    assert not plaintext.exists()
    envelope = _load_envelope(encrypted.read_bytes())
    assert encrypted.is_file()
    assert envelope["key_id"] == "k1"
    assert envelope["wrapped_data_key"]
    assert envelope["ciphertext"]
    assert b"secret-oauth" not in encrypted.read_bytes()

    with prepared_token_file("alice@example.com") as token_path:
        assert Path(token_path).read_text(encoding="utf-8") == '{"token": "secret-oauth"}'


def test_key_from_file_works_without_env_secret(monkeypatch, tmp_path) -> None:
    key_file = tmp_path / "token-key.txt"
    monkeypatch.setattr(settings, "token_encryption_key_file", _write_key_file(key_file, "file-secret"))
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "token_encryption_required", True)
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))

    with prepared_token_file("alice@example.com") as token_path:
        Path(token_path).write_text('{"token":"from-file"}', encoding="utf-8")

    with prepared_token_file("alice@example.com") as token_path:
        assert json.loads(Path(token_path).read_text(encoding="utf-8"))["token"] == "from-file"


def test_rotation_rewraps_to_new_key_id(monkeypatch, tmp_path) -> None:
    key_file = tmp_path / "token-key.json"
    monkeypatch.setattr(settings, "token_encryption_key_file", _write_key_file(key_file, {"active_key_id": "k1", "keys": {"k1": "old-secret"}}))
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "token_encryption_required", True)
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))

    with prepared_token_file("alice@example.com") as token_path:
        Path(token_path).write_text('{"token":"rotating"}', encoding="utf-8")

    encrypted = tmp_path / "token.json.enc"
    assert _load_envelope(encrypted.read_bytes())["key_id"] == "k1"

    monkeypatch.setattr(settings, "token_encryption_key_file", _write_key_file(key_file, {"active_key_id": "k2", "keys": {"k1": "old-secret", "k2": "new-secret"}}))

    with prepared_token_file("alice@example.com") as token_path:
        assert json.loads(Path(token_path).read_text(encoding="utf-8"))["token"] == "rotating"

    assert _load_envelope(encrypted.read_bytes())["key_id"] == "k2"


def test_legacy_encrypted_blob_still_decrypts(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "token_encryption_key_file", "")
    monkeypatch.setattr(settings, "token_encryption_key", "legacy-secret")
    monkeypatch.setattr(settings, "token_encryption_required", True)
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))

    target = tmp_path / "token.json"
    enc_path = _encrypted_path(target)
    enc_path.write_bytes(_derive_fernet("legacy-secret").encrypt(b'{"token":"legacy"}'))

    with prepared_token_file("alice@example.com") as token_path:
        assert json.loads(Path(token_path).read_text(encoding="utf-8"))["token"] == "legacy"

    assert _load_envelope(enc_path.read_bytes())["key_id"] == DEFAULT_KEY_ID


def test_require_encryption_without_key_raises_and_creates_no_plaintext(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "token_encryption_key_file", "")
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "token_encryption_required", True)
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))

    with pytest.raises(RuntimeError, match="Token encryption is required"):
        with prepared_token_file("alice@example.com"):
            raise AssertionError("should not yield plaintext path")

    assert not (tmp_path / "token.json").exists()
    assert not (tmp_path / "token.json.enc").exists()


def test_concurrent_prepared_token_file_contexts_serialize(monkeypatch, tmp_path) -> None:
    key_file = tmp_path / "token-key.txt"
    monkeypatch.setattr(settings, "token_encryption_key_file", _write_key_file(key_file, "lock-secret"))
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "token_encryption_required", True)
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))

    order: list[str] = []

    def worker(name: str, sleep_time: float):
        with prepared_token_file("alice@example.com") as token_path:
            order.append(f"enter:{name}")
            path = Path(token_path)
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            path.write_text(current + name, encoding="utf-8")
            time.sleep(sleep_time)
            order.append(f"exit:{name}")

    t1 = threading.Thread(target=worker, args=("A", 0.2))
    t2 = threading.Thread(target=worker, args=("B", 0.0))
    t1.start()
    time.sleep(0.05)
    t2.start()
    t1.join()
    t2.join()

    assert order == ["enter:A", "exit:A", "enter:B", "exit:B"]
    with prepared_token_file("alice@example.com") as token_path:
        assert Path(token_path).read_text(encoding="utf-8") == "AB"


def test_delete_token_removes_plaintext_encrypted_and_lock(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))
    target = tmp_path / "token.json"
    target.write_text("{}")
    target.with_name(target.name + ".enc").write_text("enc")
    target.with_name(target.name + ".lock").write_text("lock")

    assert delete_token() is True
    assert not target.exists()
    assert not target.with_name(target.name + ".enc").exists()
    assert not target.with_name(target.name + ".lock").exists()


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


def test_encryption_failure_keeps_plaintext_token(monkeypatch, tmp_path) -> None:
    """A failed at-rest encryption must never destroy the only copy of the token."""
    import src.token_store as token_store

    monkeypatch.setattr(settings, "token_encryption_key_file", "")
    monkeypatch.setattr(settings, "token_encryption_key", "unit-test-secret")
    monkeypatch.setattr(settings, "token_encryption_required", True)
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))

    def broken_encrypt(*args, **kwargs):
        raise RuntimeError("simulated encryption failure")

    monkeypatch.setattr(token_store, "_encrypt_envelope", broken_encrypt)
    with prepared_token_file("alice@example.com") as token_path:
        Path(token_path).write_text('{"token": "fresh-oauth"}', encoding="utf-8")

    plaintext = tmp_path / "token.json"
    assert plaintext.is_file()
    assert json.loads(plaintext.read_text())["token"] == "fresh-oauth"
    assert not (tmp_path / "token.json.enc").exists()

    # Once encryption works again the surviving plaintext is re-encrypted normally.
    monkeypatch.undo()
    monkeypatch.setattr(settings, "token_encryption_key_file", "")
    monkeypatch.setattr(settings, "token_encryption_key", "unit-test-secret")
    monkeypatch.setattr(settings, "token_encryption_required", True)
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))
    with prepared_token_file("alice@example.com") as token_path:
        assert json.loads(Path(token_path).read_text())["token"] == "fresh-oauth"
    assert not plaintext.exists()
    assert (tmp_path / "token.json.enc").is_file()


def test_surviving_plaintext_wins_over_stale_encrypted_blob(monkeypatch, tmp_path) -> None:
    """When both a plaintext token and an older .enc blob exist, the plaintext is newer."""
    import src.token_store as token_store

    monkeypatch.setattr(settings, "token_encryption_key_file", "")
    monkeypatch.setattr(settings, "token_encryption_key", "unit-test-secret")
    monkeypatch.setattr(settings, "token_encryption_required", True)
    monkeypatch.setattr(settings, "tenant_mode", "multi")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))

    with prepared_token_file("alice@example.com") as token_path:
        Path(token_path).write_text('{"token": "old-oauth"}', encoding="utf-8")
    # Simulate a newer plaintext left behind by a failed encryption pass.
    (tmp_path / "token.json").write_text('{"token": "new-oauth"}', encoding="utf-8")

    with prepared_token_file("alice@example.com") as token_path:
        assert json.loads(Path(token_path).read_text())["token"] == "new-oauth"
