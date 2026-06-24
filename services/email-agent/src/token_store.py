from __future__ import annotations

import base64
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.config import SERVICE_ROOT, settings
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
)


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
    resolved = normalize_user_id(user_id or current_user_id())
    resolved_instance = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    default_user = normalize_user_id(settings.default_user_id)
    default_instance = normalize_agent_instance_id(settings.default_agent_instance_id)
    if resolved_instance == default_instance:
        if settings.tenant_mode == "single" and resolved == default_user:
            return _service_path(settings.gmail_token_path)
        token_dir = _token_store_dir()
        token_dir.mkdir(parents=True, exist_ok=True)
        return token_dir / f"{resolved}.json"

    token_dir = _token_store_dir()
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir / f"{resolved}__{resolved_instance}.json"


def _fernet():
    from cryptography.fernet import Fernet

    # Accept any passphrase: derive a stable 32-byte urlsafe-base64 Fernet key from it.
    digest = hashlib.sha256(settings.token_encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypted_path(target: Path) -> Path:
    return target.with_name(target.name + ".enc")


@contextmanager
def prepared_token_file(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> Iterator[str]:
    """Yield a plaintext token path for the Google client, encrypting it at rest.

    When AGENT_TOKEN_ENCRYPTION_KEY is unset this is a passthrough (current behavior).
    When set, the persisted token lives as a Fernet-encrypted ``<token>.enc`` blob:
    it is decrypted to the plaintext path for the duration of the call and the
    plaintext is re-encrypted and removed on exit, so tokens are never left on disk
    in the clear.
    """
    target = token_file_for_user(user_id, agent_instance_id)
    if not settings.token_encryption_key:
        yield str(target)
        return

    fernet = _fernet()
    enc_path = _encrypted_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if enc_path.is_file():
        target.write_bytes(fernet.decrypt(enc_path.read_bytes()))
    try:
        yield str(target)
    finally:
        if target.is_file():
            enc_path.write_bytes(fernet.encrypt(target.read_bytes()))
            target.unlink()
