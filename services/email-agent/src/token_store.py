from __future__ import annotations

import base64
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.config import SERVICE_ROOT, settings
from src.tenant import (
    current_agent_instance_id,
    normalize_agent_instance_id,
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


def _fernet():
    from cryptography.fernet import Fernet

    # Accept any passphrase: derive a stable 32-byte urlsafe-base64 Fernet key from it.
    digest = hashlib.sha256(settings.token_encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypted_path(target: Path) -> Path:
    return target.with_name(target.name + ".enc")


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
    removed = False
    for path in (target, enc_path):
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

    When AGENT_TOKEN_ENCRYPTION_KEY is unset this is a passthrough (current behavior).
    When set, the persisted token lives as a Fernet-encrypted ``<token>.enc`` blob:
    it is decrypted to the plaintext path for the duration of the call and the
    plaintext is re-encrypted and removed on exit, so tokens are never left on disk
    in the clear.

    Concurrency note: the decrypt→use→re-encrypt sequence is not protected by a file
    lock. For the single-process dev setup (one uvicorn worker + one poller) this is
    safe in practice. In a multi-process production deployment, move to a DB-backed
    token store with row-level locking or use an external secrets manager.
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
