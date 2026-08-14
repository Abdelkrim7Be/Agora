from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, Field

from src.config import SERVICE_ROOT, settings
from src.instance_config import read_instance_text, write_instance_text

DEFAULT_RUNTIME_SETTINGS_PATH = SERVICE_ROOT / "runtime_settings.yaml"


class RuntimeSettings(BaseModel):
    sync_limit: int = Field(default=settings.max_emails_per_run, ge=1, le=500)
    setup_recent_limit: int = Field(default=settings.setup_recent_limit, ge=1, le=500)
    setup_backlog_limit: int = Field(default=settings.setup_backlog_limit, ge=1, le=500)
    setup_sent_sample: int = Field(default=settings.setup_sent_sample, ge=1, le=500)
    # Fed whole into a single style-learning prompt, unlike the fields above (one call
    # per message) — a small local model's context window caps this well below 500.
    style_sent_sample: int = Field(default=8, ge=1, le=50)


def _defaults() -> dict[str, int]:
    return {
        "sync_limit": settings.max_emails_per_run,
        "setup_recent_limit": settings.setup_recent_limit,
        "setup_backlog_limit": settings.setup_backlog_limit,
        "setup_sent_sample": settings.setup_sent_sample,
        "style_sent_sample": 8,
    }


def load_runtime_settings(agent_instance_id: str | None = None) -> RuntimeSettings:
    raw = read_instance_text("runtime_settings", DEFAULT_RUNTIME_SETTINGS_PATH, agent_instance_id)
    data: dict[str, Any] = {}
    if raw.strip():
        loaded = yaml.safe_load(raw) or {}
        if isinstance(loaded, dict):
            data = loaded
    return RuntimeSettings(**{**_defaults(), **data})


def save_runtime_settings(
    config: RuntimeSettings,
    agent_instance_id: str | None = None,
) -> RuntimeSettings:
    payload = yaml.safe_dump(config.model_dump(), sort_keys=False, allow_unicode=True)
    write_instance_text("runtime_settings", payload, DEFAULT_RUNTIME_SETTINGS_PATH, agent_instance_id)
    return config
