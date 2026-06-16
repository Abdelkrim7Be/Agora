from __future__ import annotations

from pathlib import Path

from src.config import SERVICE_ROOT, settings
from src.tenant import current_user_id, normalize_user_id


def _service_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else SERVICE_ROOT / candidate


def _token_store_dir() -> Path:
    configured = _service_path(settings.gmail_token_store_path)
    return configured.with_suffix("") if configured.suffix else configured


def token_file_for_user(user_id: str | None = None) -> Path:
    resolved = normalize_user_id(user_id or current_user_id())
    default_user = normalize_user_id(settings.default_user_id)
    if settings.tenant_mode == "single" and resolved == default_user:
        return _service_path(settings.gmail_token_path)

    token_dir = _token_store_dir()
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir / f"{resolved}.json"
