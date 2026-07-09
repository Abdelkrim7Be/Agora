from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

import yaml
from langchain.chat_models import init_chat_model
from pydantic import BaseModel

from src.config import SERVICE_ROOT, settings

REQUIRED_LLM_ROLES = ("triage", "draft", "reason", "memory_style")
DEFAULT_LLM_PROFILE = "dev"
DEFAULT_LLM_CONFIG_DIR = SERVICE_ROOT / "config"


class LlmProfile(BaseModel):
    endpoint: str | None = None
    temperature: float = 0.0
    roles: dict[str, str]


def active_profile_name() -> str:
    return os.getenv("AGENT_LLM_PROFILE", settings.llm_profile).strip() or DEFAULT_LLM_PROFILE


def active_profile_path(profile_name: str | None = None) -> Path:
    override = os.getenv("AGENT_LLM_CONFIG_PATH", settings.llm_config_path).strip()
    if override:
        path = Path(override)
        return path if path.is_absolute() else SERVICE_ROOT / path
    return DEFAULT_LLM_CONFIG_DIR / f"llm.{profile_name or active_profile_name()}.yaml"


@lru_cache(maxsize=8)
def load_llm_profile(
    profile_name: str | None = None,
    config_path: str | Path | None = None,
) -> LlmProfile:
    path = Path(config_path) if config_path else active_profile_path(profile_name)
    if not path.is_absolute():
        path = SERVICE_ROOT / path
    if not path.is_file():
        raise FileNotFoundError(f"LLM profile not found at {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profile = LlmProfile(**data)
    missing_roles = [role for role in REQUIRED_LLM_ROLES if not profile.roles.get(role)]
    if missing_roles:
        listed = ", ".join(missing_roles)
        raise ValueError(f"LLM profile {path} is missing roles: {listed}")
    return profile


def clear_llm_profile_cache() -> None:
    load_llm_profile.cache_clear()


def get_llm_model_name(
    role: str,
    *,
    profile_name: str | None = None,
    config_path: str | Path | None = None,
) -> str:
    profile = load_llm_profile(profile_name=profile_name, config_path=config_path)
    model_name = profile.roles.get(role)
    if model_name:
        return model_name
    supported = ", ".join(REQUIRED_LLM_ROLES)
    raise ValueError(f"Unsupported LLM role '{role}'. Expected one of: {supported}")


def get_llm(
    role: str,
    *,
    profile_name: str | None = None,
    config_path: str | Path | None = None,
):
    profile = load_llm_profile(profile_name=profile_name, config_path=config_path)
    model_name = profile.roles.get(role)
    if not model_name:
        supported = ", ".join(REQUIRED_LLM_ROLES)
        raise ValueError(f"Unsupported LLM role '{role}'. Expected one of: {supported}")
    kwargs = {"temperature": profile.temperature}
    if profile.endpoint:
        kwargs["base_url"] = profile.endpoint
    return init_chat_model(model_name, **kwargs)
