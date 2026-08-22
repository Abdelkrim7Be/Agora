from __future__ import annotations

from functools import lru_cache
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from src.config import LOCAL_LLM_PROFILES, SERVICE_ROOT, settings
from src.metrics import inc_counter

logger = logging.getLogger(__name__)

REQUIRED_LLM_ROLES = ("triage", "draft", "reason", "memory_style", "quarantine")
# Local-first: the platform runs on the host Ollama by default; cloud profiles
# (dev=groq, prod=litellm) stay available but must be selected explicitly.
DEFAULT_LLM_PROFILE = "local"
DEFAULT_LLM_CONFIG_DIR = SERVICE_ROOT / "config"


class RoleConfig(BaseModel):
    """Per-role override: a role may pin its own model params (e.g. a more
    creative draft role) while inheriting profile defaults for the rest."""

    model: str
    temperature: float | None = None
    max_tokens: int | None = None


class LlmProfile(BaseModel):
    endpoint: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout: float | None = None
    # A role entry is either a bare model string (legacy) or a RoleConfig mapping.
    roles: dict[str, str | RoleConfig]
    fallbacks: dict[str, list[str]] = Field(default_factory=dict)

    def role_config(self, role: str) -> RoleConfig | None:
        entry = self.roles.get(role)
        if entry is None:
            return None
        if isinstance(entry, str):
            return RoleConfig(model=entry) if entry else None
        return entry if entry.model else None


class ToollessChatModel:
    def __init__(self, candidate: Any):
        self._candidate = candidate
        self.model_name = getattr(candidate, "model_name", None)

    def __getattr__(self, item: str):
        return getattr(self._candidate, item)

    def invoke(self, messages, config=None):
        try:
            return self._candidate.invoke(messages, config=config)
        except TypeError as exc:
            if config is None or "config" not in str(exc):
                raise
            return self._candidate.invoke(messages)

    def stream(self, messages, config=None):
        try:
            yield from self._candidate.stream(messages, config=config)
        except TypeError as exc:
            if config is None or "config" not in str(exc):
                raise
            yield from self._candidate.stream(messages)

    def bind_tools(self, *args, **kwargs):
        raise RuntimeError("quarantine LLM clients cannot bind tools")

    def with_structured_output(self, *args, **kwargs):
        return ToollessChatModel(self._candidate.with_structured_output(*args, **kwargs))


class FallbackChatModel:
    def __init__(self, role: str, models: list[tuple[str, Any]]):
        self._role = role
        self._models = models
        self.model_name = models[0][0]

    def __getattr__(self, item: str):
        return getattr(self._models[0][1], item)

    def _invoke_candidate(self, candidate, messages, config=None):
        try:
            return candidate.invoke(messages, config=config)
        except TypeError as exc:
            if config is None or "config" not in str(exc):
                raise
            return candidate.invoke(messages)

    def invoke(self, messages, config=None):
        last_exc: Exception | None = None
        for index, (model_name, candidate) in enumerate(self._models):
            try:
                return self._invoke_candidate(candidate, messages, config=config)
            except Exception as exc:  # pragma: no cover - exercised through tests with fakes.
                last_exc = exc
                if index == len(self._models) - 1:
                    raise
                logger.warning(
                    f"llm: role '{self._role}' model '{model_name}' failed; trying fallback: {exc}"
                )
                next_model = self._models[index + 1][0]
                inc_counter("agora_llm_failover_total", role=self._role, from_model=model_name, to_model=next_model, mode="invoke")
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"No LLM candidates configured for role '{self._role}'")

    def stream(self, messages, config=None):
        last_exc: Exception | None = None
        for index, (model_name, candidate) in enumerate(self._models):
            try:
                try:
                    yield from candidate.stream(messages, config=config)
                except TypeError as exc:
                    if config is None or "config" not in str(exc):
                        raise
                    yield from candidate.stream(messages)
                return
            except Exception as exc:  # pragma: no cover - exercised through tests with fakes.
                last_exc = exc
                if index == len(self._models) - 1:
                    raise
                logger.warning(
                    f"llm: role '{self._role}' model '{model_name}' stream failed; trying fallback: {exc}"
                )
                next_model = self._models[index + 1][0]
                inc_counter("agora_llm_failover_total", role=self._role, from_model=model_name, to_model=next_model, mode="stream")
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"No LLM candidates configured for role '{self._role}'")

    def bind_tools(self, *args, **kwargs):
        return FallbackChatModel(
            self._role,
            [(model_name, candidate.bind_tools(*args, **kwargs)) for model_name, candidate in self._models],
        )

    def with_structured_output(self, *args, **kwargs):
        return FallbackChatModel(
            self._role,
            [
                (model_name, candidate.with_structured_output(*args, **kwargs))
                for model_name, candidate in self._models
            ],
        )


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
    missing_roles = [role for role in REQUIRED_LLM_ROLES if profile.role_config(role) is None]
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
    role_cfg = profile.role_config(role)
    if role_cfg:
        return role_cfg.model
    supported = ", ".join(REQUIRED_LLM_ROLES)
    raise ValueError(f"Unsupported LLM role '{role}'. Expected one of: {supported}")


def _extra_body(kwargs: dict[str, Any]) -> dict[str, Any]:
    body = kwargs.get("extra_body")
    if not isinstance(body, dict):
        body = {}
        kwargs["extra_body"] = body
    return body


def _prompt_cache_key(role: str, profile_name: str | None = None) -> str | None:
    active = (profile_name or active_profile_name()).strip().lower()
    if not settings.llm_prompt_cache_enabled or active in LOCAL_LLM_PROFILES:
        return None
    return f"agora-email-agent:{active}:{role}"


def _model_kwargs(
    profile: LlmProfile,
    role_cfg: RoleConfig | None = None,
    *,
    role: str = "",
    profile_name: str | None = None,
) -> dict[str, Any]:
    temperature = role_cfg.temperature if role_cfg and role_cfg.temperature is not None else profile.temperature
    max_tokens = role_cfg.max_tokens if role_cfg and role_cfg.max_tokens is not None else profile.max_tokens
    kwargs: dict[str, Any] = {"temperature": temperature}
    if profile.endpoint:
        kwargs["base_url"] = profile.endpoint
    if max_tokens is not None:
        if profile.endpoint:
            # langchain-openai serializes max_tokens as max_completion_tokens,
            # which OpenAI-compatible backends like Ollama ignore; send the raw
            # field via extra_body so local models are actually capped.
            _extra_body(kwargs)["max_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
    if profile.timeout is not None:
        kwargs["timeout"] = profile.timeout
    cache_key = _prompt_cache_key(role, profile_name=profile_name) if role else None
    if cache_key and profile.endpoint:
        _extra_body(kwargs)["prompt_cache_key"] = cache_key
    return kwargs


def _role_models(profile: LlmProfile, role: str) -> list[str]:
    role_cfg = profile.role_config(role)
    if role_cfg is None:
        supported = ", ".join(REQUIRED_LLM_ROLES)
        raise ValueError(f"Unsupported LLM role '{role}'. Expected one of: {supported}")
    seen: set[str] = set()
    ordered: list[str] = []
    for item in [role_cfg.model, *(profile.fallbacks.get(role) or [])]:
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def get_llm(
    role: str,
    *,
    profile_name: str | None = None,
    config_path: str | Path | None = None,
):
    profile = load_llm_profile(profile_name=profile_name, config_path=config_path)
    model_names = _role_models(profile, role)
    kwargs = _model_kwargs(profile, profile.role_config(role), role=role, profile_name=profile_name)
    built = [(model_name, init_chat_model(model_name, **kwargs)) for model_name in model_names]
    model = built[0][1] if len(built) == 1 else FallbackChatModel(role, built)
    if role == "quarantine":
        return ToollessChatModel(model)
    return model
