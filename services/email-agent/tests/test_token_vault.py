from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import managed_secrets
from src.config import settings
from src.token_store import (
    delete_token,
    has_stored_token,
    prepared_token_file,
    token_file_for_user,
    validate_token_security,
)


class FakeVault:
    def __init__(self):
        self.records: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_writes = False
        self.capabilities = {"create", "read", "update", "delete"}

    def __call__(
        self,
        prefix: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
    ) -> dict | None:
        assert prefix == "AGENT"
        self.calls.append((method, path))
        if path == "sys/capabilities-self":
            return {"capabilities": sorted(self.capabilities)}
        if method == "GET":
            value = self.records.get(path)
            if value is None:
                return None
            return {"data": {"data": {"token_envelope": value}}}
        if method == "POST":
            if self.fail_writes:
                raise OSError("vault unavailable")
            self.records[path] = payload["data"]["token_envelope"]
            return {}
        if method == "DELETE":
            self.records.pop(path, None)
            return {}
        raise AssertionError(f"unexpected method {method}")


@pytest.fixture
def vault(monkeypatch, tmp_path) -> FakeVault:
    fake = FakeVault()
    monkeypatch.setattr(settings, "token_store_backend", "vault")
    monkeypatch.setattr(settings, "token_encryption_required", True)
    key_file = tmp_path / "token-keyring"
    key_file.write_text("vault-envelope-key-with-at-least-32-bytes", encoding="utf-8")
    key_file.chmod(0o600)
    monkeypatch.setattr(settings, "token_encryption_key_file", str(key_file))
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "token_work_dir", str(tmp_path / "work"))
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))
    monkeypatch.setattr(
        settings,
        "gmail_token_store_path",
        str(tmp_path / "gmail_tokens.json"),
    )
    monkeypatch.setattr(
        settings,
        "token_vault_path",
        "secret/data/agora/gmail-tokens",
    )
    monkeypatch.setattr(managed_secrets, "vault_request", fake)
    return fake


def test_vault_round_trip_leaves_no_local_token(vault, tmp_path):
    with prepared_token_file(agent_instance_id="default-email-agent") as token_path:
        path = Path(token_path)
        path.write_text(
            json.dumps({"token": "oauth-secret", "refresh_token": "refresh-secret"}),
            encoding="utf-8",
        )
        assert path.stat().st_mode & 0o777 == 0o600

    target = token_file_for_user(agent_instance_id="default-email-agent")
    record = next(iter(vault.records.values()))
    assert "oauth-secret" not in record
    assert "refresh-secret" not in record
    assert not target.exists()
    assert not target.with_name(target.name + ".enc").exists()
    assert list((tmp_path / "work").iterdir()) == []

    with prepared_token_file(agent_instance_id="default-email-agent") as token_path:
        restored = json.loads(Path(token_path).read_text(encoding="utf-8"))
        assert restored["refresh_token"] == "refresh-secret"


def test_vault_write_failure_preserves_old_envelope_and_cleans_plaintext(vault, tmp_path):
    with prepared_token_file(agent_instance_id="default-email-agent") as token_path:
        Path(token_path).write_text(
            json.dumps({"token": "old-token"}),
            encoding="utf-8",
        )
    vault_path, old_envelope = next(iter(vault.records.items()))

    vault.fail_writes = True
    with pytest.raises(OSError, match="vault unavailable"):
        with prepared_token_file(agent_instance_id="default-email-agent") as token_path:
            Path(token_path).write_text(
                json.dumps({"token": "new-token"}),
                encoding="utf-8",
            )

    assert vault.records[vault_path] == old_envelope
    assert list((tmp_path / "work").iterdir()) == []

    vault.fail_writes = False
    with prepared_token_file(agent_instance_id="default-email-agent") as token_path:
        assert json.loads(Path(token_path).read_text())["token"] == "old-token"


def test_has_and_delete_token_use_vault(vault):
    assert has_stored_token("default-email-agent") is False

    with prepared_token_file(agent_instance_id="default-email-agent") as token_path:
        Path(token_path).write_text(json.dumps({"token": "stored"}))

    assert has_stored_token("default-email-agent") is True
    assert delete_token(agent_instance_id="default-email-agent") is True
    assert has_stored_token("default-email-agent") is False


def test_startup_validation_migrates_legacy_plaintext(vault, tmp_path):
    target = token_file_for_user(agent_instance_id="default-email-agent")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"token": "legacy-plaintext"}), encoding="utf-8")

    validate_token_security()

    assert not target.exists()
    assert vault.records
    assert "legacy-plaintext" not in next(iter(vault.records.values()))
    assert list((tmp_path / "work").iterdir()) == []


def test_vault_backend_requires_encryption_enforcement(vault, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_required", False)

    with pytest.raises(RuntimeError, match="requires token encryption"):
        validate_token_security()


def test_vault_backend_requires_master_key(vault, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key_file", "/missing/keyring")

    with pytest.raises(FileNotFoundError):
        validate_token_security()


def test_unknown_backend_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "token_store_backend", "unknown")

    with pytest.raises(RuntimeError, match="Unsupported AGENT_TOKEN_STORE_BACKEND"):
        validate_token_security()


def test_vault_path_is_scoped_by_normalized_instance(vault):
    with prepared_token_file(agent_instance_id="../CEO Mailbox") as token_path:
        Path(token_path).write_text(json.dumps({"token": "scoped"}))

    assert any(
        path.endswith("/CEO_Mailbox")
        for method, path in vault.calls
        if method == "POST"
    )


def test_vault_backend_requires_mounted_keyring(vault, monkeypatch):
    monkeypatch.setattr(settings, "token_encryption_key_file", "")
    monkeypatch.setattr(settings, "token_encryption_key", "env-secret")

    with pytest.raises(RuntimeError, match="mounted AGENT_TOKEN_ENCRYPTION_KEY_FILE"):
        validate_token_security()


def test_vault_backend_requires_full_token_policy(vault):
    vault.capabilities.remove("delete")

    with pytest.raises(RuntimeError, match="missing capabilities: delete"):
        validate_token_security()


def test_vault_backend_rejects_weak_encryption_key(vault):
    Path(settings.token_encryption_key_file).write_text("too-short", encoding="utf-8")

    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        validate_token_security()
