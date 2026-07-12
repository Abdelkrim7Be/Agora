from __future__ import annotations

import base64
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.config import SERVICE_ROOT, settings
from src.tenant import (
    current_agent_instance_id,
    normalize_agent_instance_id,
)

ENVELOPE_VERSION = 2
DEFAULT_KEY_ID = "default"
LEGACY_KEY_ID = "legacy"


def _service_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else SERVICE_ROOT / candidate


def _token_store_dir() -> Path:
    configured = _service_path(settings.gmail_token_store_path)
    return configured.with_suffix("") if configured.suffix else configured


def token_file_for_user(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> Path:
    """Return the OAuth token path shared by one agent instance.

    ``user_id`` remains accepted for API compatibility, but delegated users must
    resolve the same mailbox token for a given instance.
    """
    resolved_instance = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    default_instance = normalize_agent_instance_id(settings.default_agent_instance_id)
    if resolved_instance == default_instance:
        return _service_path(settings.gmail_token_path)

    token_dir = _token_store_dir()
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir / f"instance__{resolved_instance}.json"


def _derive_fernet(secret: str):
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _key_config_from_file(path: str) -> tuple[str, dict[str, str]]:
    candidate = _service_path(path)
    raw = candidate.read_text(encoding="utf-8").strip()
    if not raw:
        return DEFAULT_KEY_ID, {}
    if raw[0] not in "[{":
        return DEFAULT_KEY_ID, {DEFAULT_KEY_ID: raw}

    parsed = json.loads(raw)
    if isinstance(parsed, dict) and isinstance(parsed.get("keys"), dict):
        keys = {str(k): str(v).strip() for k, v in parsed["keys"].items() if str(v).strip()}
        active_key_id = str(parsed.get("active_key_id") or next(iter(keys), DEFAULT_KEY_ID))
        if active_key_id not in keys and keys:
            active_key_id = next(iter(keys))
        return active_key_id, keys
    if isinstance(parsed, dict):
        keys = {str(k): str(v).strip() for k, v in parsed.items() if str(v).strip()}
        active_key_id = next(iter(keys), DEFAULT_KEY_ID)
        return active_key_id, keys
    raise ValueError("AGENT_TOKEN_ENCRYPTION_KEY_FILE must contain a secret string or JSON object")


def _keyring() -> tuple[str | None, dict[str, str]]:
    key_file = settings.token_encryption_key_file.strip()
    if key_file:
        active_key_id, keys = _key_config_from_file(key_file)
        if not keys:
            return None, {}
        return active_key_id, keys
    secret = (settings.token_encryption_key or "").strip()
    if not secret:
        return None, {}
    return DEFAULT_KEY_ID, {DEFAULT_KEY_ID: secret, LEGACY_KEY_ID: secret}


def active_master_key_secret() -> str:
    active_key_id, keys = _keyring()
    if not keys:
        return ""
    return keys.get(active_key_id or DEFAULT_KEY_ID, "")


def _require_encryption_key() -> tuple[str, dict[str, str]]:
    active_key_id, keys = _keyring()
    if keys:
        return active_key_id or DEFAULT_KEY_ID, keys
    if settings.token_encryption_required:
        raise RuntimeError(
            "Token encryption is required but no key is configured. Set AGENT_TOKEN_ENCRYPTION_KEY_FILE to a mounted secret path or AGENT_TOKEN_ENCRYPTION_KEY for back-compat."
        )
    return None, {}


def _encrypted_path(target: Path) -> Path:
    return target.with_name(target.name + ".enc")


def _lock_path(target: Path) -> Path:
    return target.with_name(target.name + ".lock")


@contextmanager
def _file_lock(target: Path) -> Iterator[None]:
    import fcntl

    lock_path = _lock_path(target)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_envelope(blob: bytes) -> dict | None:
    try:
        payload = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    required = {"key_id", "wrapped_data_key", "ciphertext"}
    if not required.issubset(payload):
        return None
    return payload


def _encrypt_envelope(data: bytes, *, key_id: str, secret: str) -> bytes:
    from cryptography.fernet import Fernet

    data_key = Fernet.generate_key()
    wrapped_data_key = _derive_fernet(secret).encrypt(data_key).decode("utf-8")
    ciphertext = Fernet(data_key).encrypt(data).decode("utf-8")
    envelope = {
        "version": ENVELOPE_VERSION,
        "key_id": key_id,
        "wrapped_data_key": wrapped_data_key,
        "ciphertext": ciphertext,
    }
    return json.dumps(envelope, sort_keys=True).encode("utf-8")


def _decrypt_envelope(blob: bytes, keys: dict[str, str]) -> bytes:
    from cryptography.fernet import Fernet

    envelope = _load_envelope(blob)
    if envelope is None:
        for key_id in (LEGACY_KEY_ID, DEFAULT_KEY_ID):
            secret = keys.get(key_id)
            if not secret:
                continue
            try:
                return _derive_fernet(secret).decrypt(blob)
            except Exception:
                continue
        for secret in keys.values():
            try:
                return _derive_fernet(secret).decrypt(blob)
            except Exception:
                continue
        raise ValueError("Stored Gmail token could not be decrypted with the configured legacy key")

    key_id = str(envelope.get("key_id") or "")
    secret = keys.get(key_id)
    if not secret:
        raise ValueError(
            f"Stored Gmail token references unknown key id '{key_id}'. Reconnect Gmail or restore the matching master key."
        )
    data_key = _derive_fernet(secret).decrypt(str(envelope["wrapped_data_key"]).encode("utf-8"))
    return Fernet(data_key).decrypt(str(envelope["ciphertext"]).encode("utf-8"))


def has_stored_token(agent_instance_id: str | None = None) -> bool:
    """Return whether an instance has a plaintext or encrypted Gmail token."""
    target = token_file_for_user(agent_instance_id=agent_instance_id)
    return target.is_file() or _encrypted_path(target).is_file()


def delete_token(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> bool:
    """Delete the stored OAuth token for the given user/instance.

    Removes both the plaintext and encrypted-at-rest variants. Returns True if any
    file was removed, False if no token was present.
    """
    target = token_file_for_user(user_id, agent_instance_id)
    enc_path = _encrypted_path(target)
    lock_path = _lock_path(target)
    removed = False
    for path in (target, enc_path, lock_path):
        if path.is_file():
            path.unlink()
            removed = True
    return removed


@contextmanager
def prepared_token_file(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> Iterator[str]:
    """Yield a plaintext token path for the Google client, encrypting it at rest.

    When no key is configured and encryption is not required this remains a dev-mode
    passthrough. When a key is present, the persisted token lives as an envelope
    encrypted ``<token>.enc`` blob; the plaintext exists only inside the locked
    context window and is always re-encrypted and removed on exit.
    """
    target = token_file_for_user(user_id, agent_instance_id)
    active_key_id, keys = _require_encryption_key()
    if not keys:
        yield str(target)
        return

    enc_path = _encrypted_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(target):
        if enc_path.is_file():
            target.write_bytes(_decrypt_envelope(enc_path.read_bytes(), keys))
        try:
            yield str(target)
        finally:
            try:
                if target.is_file():
                    active_secret = keys[active_key_id]
                    enc_path.write_bytes(
                        _encrypt_envelope(target.read_bytes(), key_id=active_key_id, secret=active_secret)
                    )
            finally:
                if target.is_file():
                    target.unlink()
