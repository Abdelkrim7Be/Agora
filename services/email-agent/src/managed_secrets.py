from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any
from urllib import request

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_cache_lock = Lock()
_cache: dict[tuple[str, str, str], dict[str, str]] = {}


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() == "true"


def _service_path(raw: str) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else _SERVICE_ROOT / candidate


def _backend(prefix: str) -> str:
    return os.getenv(f"{prefix}_SECRET_MANAGER_BACKEND", "disabled").strip().lower() or "disabled"


def _required(prefix: str) -> bool:
    return _env_bool(f"{prefix}_SECRET_MANAGER_REQUIRED", "false")


def _bundle_path(prefix: str) -> str:
    return os.getenv(f"{prefix}_SECRET_BUNDLE_PATH", "").strip()


def _vault_url(prefix: str) -> str:
    return os.getenv(f"{prefix}_SECRET_MANAGER_URL", "").strip()


def _vault_path(prefix: str) -> str:
    return os.getenv(f"{prefix}_SECRET_MANAGER_PATH", "").strip()


def _vault_token(prefix: str) -> str:
    direct = os.getenv(f"{prefix}_SECRET_MANAGER_TOKEN", "").strip()
    if direct:
        return direct
    token_file = os.getenv(f"{prefix}_SECRET_MANAGER_TOKEN_FILE", "").strip()
    if not token_file:
        return ""
    candidate = _service_path(token_file)
    return candidate.read_text(encoding="utf-8").strip() if candidate.is_file() else ""


def _load_bundle(prefix: str) -> dict[str, str]:
    path = _bundle_path(prefix)
    if not path:
        return {}
    candidate = _service_path(path)
    if not candidate.is_file():
        return {}
    data = json.loads(candidate.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{prefix}_SECRET_BUNDLE_PATH must contain a JSON object")
    return {str(key): str(value) for key, value in data.items()}


def _fetch_vault(prefix: str) -> dict[str, str]:
    url = _vault_url(prefix)
    path = _vault_path(prefix)
    token = _vault_token(prefix)
    if not url or not path or not token:
        return {}
    req = request.Request(
        url.rstrip("/") + "/v1/" + path.lstrip("/"),
        headers={"X-Vault-Token": token},
    )
    with request.urlopen(req, timeout=5) as response:  # pragma: no cover - mocked in tests
        payload = json.loads(response.read().decode("utf-8"))
    data: Any = payload.get("data") or {}
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict):
        raise ValueError("Vault response did not contain a secret object")
    return {str(key): str(value) for key, value in data.items()}


def _managed_values(prefix: str) -> dict[str, str]:
    backend = _backend(prefix)
    cache_key = (prefix, backend, _bundle_path(prefix) or _vault_path(prefix))
    with _cache_lock:
        if cache_key in _cache:
            return dict(_cache[cache_key])
    if backend == "disabled":
        values = {}
    elif backend == "bundle":
        values = _load_bundle(prefix)
    elif backend == "vault":
        values = _fetch_vault(prefix)
    else:
        raise RuntimeError(f"Unsupported {prefix}_SECRET_MANAGER_BACKEND: {backend}")
    with _cache_lock:
        _cache[cache_key] = dict(values)
    return values


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def get_secret(prefix: str, name: str, *, default: str = "", legacy_env_name: str | None = None) -> str:
    managed = _managed_values(prefix)
    if name in managed and managed[name]:
        return managed[name]
    if _required(prefix):
        raise RuntimeError(f"Managed secret '{name}' is required for {prefix} but was not provided")
    legacy_names = [legacy_env_name] if legacy_env_name else [name]
    for env_name in legacy_names:
        raw = os.getenv(env_name or "", "").strip()
        if raw:
            return raw
    return default
