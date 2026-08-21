from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from src import managed_secrets
from src.config import SERVICE_ROOT, settings
from src.tenant import current_agent_instance_id, normalize_agent_instance_id

ENVELOPE_VERSION = 3
DEFAULT_KEY_ID = "default"
LEGACY_KEY_ID = "legacy"
TOKEN_BACKENDS = {"file", "vault"}
# Gmail predates the provider layer, so it keeps the unsuffixed storage key.
DEFAULT_TOKEN_PROVIDER = "gmail"


def _service_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else SERVICE_ROOT / candidate


def _token_store_dir() -> Path:
    configured = _service_path(settings.gmail_token_store_path)
    return configured.with_suffix("") if configured.suffix else configured


def token_scope(agent_instance_id: str | None = None, provider: str = DEFAULT_TOKEN_PROVIDER) -> str:
    """The opaque key a stored token lives under.

    Gmail keeps the bare instance id so every path — file name, lock file, Vault
    secret — stays byte-identical to what it was before a second provider
    existed, and no already-stored token has to move. Anything else is suffixed.
    """
    resolved_instance = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    cleaned = (provider or DEFAULT_TOKEN_PROVIDER).strip().lower()
    if cleaned in ("", DEFAULT_TOKEN_PROVIDER):
        return resolved_instance
    return f"{resolved_instance}__{cleaned}"


def token_file_for_user(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
    provider: str = DEFAULT_TOKEN_PROVIDER,
) -> Path:
    """Return the legacy logical token path for one agent instance."""
    scope = token_scope(agent_instance_id, provider)
    default_instance = normalize_agent_instance_id(settings.default_agent_instance_id)
    if scope == default_instance:
        return _service_path(settings.gmail_token_path)

    token_dir = _token_store_dir()
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir / f"instance__{scope}.json"


def _derive_fernet(secret: str):
    from cryptography.fernet import Fernet

    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _derive_tenant_fernet(secret: str, tenant_scope: str):
    from cryptography.fernet import Fernet

    digest = hmac.new(
        secret.encode("utf-8"),
        f"agora-token-tenant-wrap-v1:{normalize_agent_instance_id(tenant_scope)}".encode(
            "utf-8"
        ),
        hashlib.sha256,
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _key_config_from_raw(raw: str) -> tuple[str, dict[str, str]]:
    raw = raw.strip()
    if not raw:
        return DEFAULT_KEY_ID, {}
    if raw[0] not in "[{":
        return DEFAULT_KEY_ID, {DEFAULT_KEY_ID: raw}

    parsed = json.loads(raw)
    if isinstance(parsed, dict) and isinstance(parsed.get("keys"), dict):
        keys = {
            str(key): str(value).strip()
            for key, value in parsed["keys"].items()
            if str(value).strip()
        }
        active_key_id = str(
            parsed.get("active_key_id") or next(iter(keys), DEFAULT_KEY_ID)
        )
        if active_key_id not in keys and keys:
            active_key_id = next(iter(keys))
        return active_key_id, keys
    if isinstance(parsed, dict):
        keys = {
            str(key): str(value).strip()
            for key, value in parsed.items()
            if str(value).strip()
        }
        return next(iter(keys), DEFAULT_KEY_ID), keys
    raise ValueError("Token keyring must contain a secret string or JSON object")


def _key_config_from_file(path: str) -> tuple[str, dict[str, str]]:
    candidate = _service_path(path)
    return _key_config_from_raw(candidate.read_text(encoding="utf-8"))


def _keyring() -> tuple[str | None, dict[str, str]]:
    key_file = settings.token_encryption_key_file.strip()
    if key_file:
        active_key_id, keys = _key_config_from_file(key_file)
        return (active_key_id, keys) if keys else (None, {})

    secret = (settings.token_encryption_key or "").strip()
    if not secret:
        return None, {}
    active_key_id, keys = _key_config_from_raw(secret)
    if len(keys) == 1 and DEFAULT_KEY_ID in keys:
        keys[LEGACY_KEY_ID] = keys[DEFAULT_KEY_ID]
    return active_key_id, keys


def active_master_key_secret() -> str:
    active_key_id, keys = _keyring()
    if not keys:
        return ""
    return keys.get(active_key_id or DEFAULT_KEY_ID, "")


def _require_encryption_key() -> tuple[str | None, dict[str, str]]:
    active_key_id, keys = _keyring()
    if keys:
        return active_key_id or DEFAULT_KEY_ID, keys
    if settings.token_encryption_required:
        raise RuntimeError(
            "Token encryption is required but no key is configured. Set "
            "AGENT_TOKEN_ENCRYPTION_KEY_FILE to a mounted secret path."
        )
    return None, {}


def _backend() -> str:
    backend = (settings.token_store_backend or "file").strip().lower()
    if backend not in TOKEN_BACKENDS:
        raise RuntimeError(f"Unsupported AGENT_TOKEN_STORE_BACKEND: {backend}")
    return backend


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
    return payload if required.issubset(payload) else None


def _encrypt_envelope(
    data: bytes,
    *,
    key_id: str,
    secret: str,
    tenant_scope: str,
) -> bytes:
    from cryptography.fernet import Fernet

    data_key = Fernet.generate_key()
    normalized_scope = normalize_agent_instance_id(tenant_scope)
    wrapped_data_key = (
        _derive_tenant_fernet(secret, normalized_scope)
        .encrypt(data_key)
        .decode("utf-8")
    )
    ciphertext = Fernet(data_key).encrypt(data).decode("utf-8")
    envelope = {
        "version": ENVELOPE_VERSION,
        "key_id": key_id,
        "tenant_scope": normalized_scope,
        "wrapped_data_key": wrapped_data_key,
        "ciphertext": ciphertext,
    }
    return json.dumps(envelope, sort_keys=True).encode("utf-8")


def _decrypt_envelope(
    blob: bytes,
    keys: dict[str, str],
    *,
    tenant_scope: str | None = None,
) -> bytes:
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
        raise ValueError(
            "Stored Gmail token could not be decrypted with the configured legacy key"
        )

    key_id = str(envelope.get("key_id") or "")
    secret = keys.get(key_id)
    if not secret:
        raise ValueError(
            f"Stored Gmail token references unknown key id {key_id}. "
            "Restore the matching master key or reconnect Gmail."
        )
    stored_scope = envelope.get("tenant_scope")
    if stored_scope:
        normalized_stored_scope = normalize_agent_instance_id(str(stored_scope))
        if (
            tenant_scope is not None
            and normalize_agent_instance_id(tenant_scope) != normalized_stored_scope
        ):
            raise ValueError(
                "Stored Gmail token belongs to a different tenant scope. "
                "Reconnect Gmail for this agent instance."
            )
        wrapper = _derive_tenant_fernet(secret, normalized_stored_scope)
    else:
        wrapper = _derive_fernet(secret)
    data_key = wrapper.decrypt(str(envelope["wrapped_data_key"]).encode("utf-8"))
    return Fernet(data_key).decrypt(str(envelope["ciphertext"]).encode("utf-8"))


def _vault_path(agent_instance_id: str) -> str:
    base = settings.token_vault_path.strip().rstrip("/")
    if not base:
        raise RuntimeError("AGENT_TOKEN_VAULT_PATH is required for the Vault backend")
    encoded_instance = quote(normalize_agent_instance_id(agent_instance_id), safe="")
    return f"{base}/{encoded_instance}"


def _vault_blob(agent_instance_id: str) -> bytes | None:
    payload = managed_secrets.vault_request("AGENT", _vault_path(agent_instance_id))
    if payload is None:
        return None
    data = payload.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    value = data.get("token_envelope") if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError("Vault token record did not contain token_envelope")
    return value.encode("utf-8")


def _write_vault_blob(agent_instance_id: str, blob: bytes) -> None:
    managed_secrets.vault_request(
        "AGENT",
        _vault_path(agent_instance_id),
        method="POST",
        payload={"data": {"token_envelope": blob.decode("utf-8")}},
    )


def _delete_vault_blob(agent_instance_id: str) -> None:
    managed_secrets.vault_request(
        "AGENT",
        _vault_path(agent_instance_id),
        method="DELETE",
    )


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_persisted_blob(target: Path, agent_instance_id: str) -> bytes | None:
    if _backend() == "vault":
        blob = _vault_blob(agent_instance_id)
        if blob is not None:
            return blob
    enc_path = _encrypted_path(target)
    return enc_path.read_bytes() if enc_path.is_file() else None


def _persist_blob(target: Path, agent_instance_id: str, blob: bytes) -> None:
    if _backend() == "vault":
        _write_vault_blob(agent_instance_id, blob)
        _encrypted_path(target).unlink(missing_ok=True)
    else:
        _atomic_write(_encrypted_path(target), blob)
    target.unlink(missing_ok=True)


def _temporary_token_path(target: Path) -> Path:
    work_dir = Path(settings.token_work_dir)
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    work_dir.chmod(0o700)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f"{target.stem}-",
        suffix=".json",
        dir=work_dir,
    )
    os.close(descriptor)
    path = Path(raw_path)
    path.chmod(0o600)
    return path


def has_stored_token(
    agent_instance_id: str | None = None,
    provider: str = DEFAULT_TOKEN_PROVIDER,
) -> bool:
    scope = token_scope(agent_instance_id, provider)
    target = token_file_for_user(agent_instance_id=agent_instance_id, provider=provider)
    if target.is_file() or _encrypted_path(target).is_file():
        return True
    return _vault_blob(scope) is not None if _backend() == "vault" else False


def delete_token(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
    provider: str = DEFAULT_TOKEN_PROVIDER,
) -> bool:
    scope = token_scope(agent_instance_id, provider)
    target = token_file_for_user(user_id, agent_instance_id, provider=provider)
    removed = target.is_file() or _encrypted_path(target).is_file()

    if _backend() == "vault":
        vault_exists = _vault_blob(scope) is not None
        if vault_exists:
            _delete_vault_blob(scope)
        removed = removed or vault_exists

    for path in (target, _encrypted_path(target), _lock_path(target)):
        if path.is_file():
            path.unlink()
    return removed


@contextmanager
def prepared_token_file(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
    provider: str = DEFAULT_TOKEN_PROVIDER,
) -> Iterator[str]:
    """Yield a temporary plaintext token and persist only an encrypted envelope."""
    resolved_instance = token_scope(agent_instance_id, provider)
    target = token_file_for_user(user_id, agent_instance_id, provider=provider)
    active_key_id, keys = _require_encryption_key()
    backend = _backend()

    if backend == "vault" and not settings.token_encryption_required:
        raise RuntimeError(
            "Vault token storage requires AGENT_TOKEN_ENCRYPTION_REQUIRED=true"
        )
    if not keys:
        yield str(target)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(target):
        work_path = _temporary_token_path(target)
        try:
            if target.is_file():
                work_path.write_bytes(target.read_bytes())
                work_path.chmod(0o600)
            else:
                blob = _read_persisted_blob(target, resolved_instance)
                if blob is not None:
                    work_path.write_bytes(
                        _decrypt_envelope(blob, keys, tenant_scope=resolved_instance)
                    )
                    work_path.chmod(0o600)

            yield str(work_path)

            if work_path.is_file() and work_path.stat().st_size > 0:
                secret = keys[active_key_id]
                blob = _encrypt_envelope(
                    work_path.read_bytes(),
                    key_id=active_key_id,
                    secret=secret,
                    tenant_scope=resolved_instance,
                )
                _persist_blob(target, resolved_instance, blob)
        finally:
            work_path.unlink(missing_ok=True)


def _known_local_token_targets() -> list[tuple[str, Path]]:
    default_instance = normalize_agent_instance_id(settings.default_agent_instance_id)
    targets: dict[Path, str] = {
        token_file_for_user(agent_instance_id=default_instance): default_instance
    }
    token_dir = _token_store_dir()
    if token_dir.is_dir():
        for candidate in token_dir.glob("instance__*.json*"):
            name = candidate.name.removesuffix(".enc").removesuffix(".lock")
            if not name.startswith("instance__") or not name.endswith(".json"):
                continue
            instance_id = name[len("instance__") : -len(".json")]
            targets[candidate.with_name(name)] = normalize_agent_instance_id(instance_id)
    return [(instance_id, path) for path, instance_id in targets.items()]


def migrate_local_tokens_to_vault() -> int:
    if _backend() != "vault":
        return 0
    migrated = 0
    for instance_id, target in _known_local_token_targets():
        if not target.is_file() and not _encrypted_path(target).is_file():
            continue
        with prepared_token_file(agent_instance_id=instance_id):
            pass
        migrated += 1
    return migrated


def validate_token_security() -> None:
    backend = _backend()
    if backend != "vault":
        if settings.token_encryption_required:
            _require_encryption_key()
        return

    if not settings.token_encryption_required:
        raise RuntimeError(
            "Production Vault token storage requires token encryption enforcement"
        )
    if not settings.token_encryption_key_file.strip():
        raise RuntimeError(
            "Production Vault token storage requires a mounted "
            "AGENT_TOKEN_ENCRYPTION_KEY_FILE"
        )
    active_key_id, keys = _require_encryption_key()
    active_secret = keys[active_key_id or DEFAULT_KEY_ID]
    if len(active_secret.encode("utf-8")) < 32:
        raise RuntimeError(
            "Production token encryption key must contain at least 32 bytes"
        )
    default_instance = normalize_agent_instance_id(settings.default_agent_instance_id)
    default_path = _vault_path(default_instance)
    required_capabilities = {"create", "read", "update", "delete"}
    capabilities = managed_secrets.vault_capabilities("AGENT", default_path)
    missing = required_capabilities - capabilities
    if missing:
        raise RuntimeError(
            "Vault token policy is missing capabilities: " + ", ".join(sorted(missing))
        )
    # Verify the KV data endpoint before serving traffic.
    _vault_blob(default_instance)
    migrate_local_tokens_to_vault()
