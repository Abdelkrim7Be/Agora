from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Any

import yaml
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from src.config import SERVICE_ROOT, settings
from src.metrics import inc_counter

REQUIRED_LLM_ROLES = ("triage", "draft", "reason", "memory_style")
DEFAULT_LLM_PROFILE = "dev"
DEFAULT_LLM_CONFIG_DIR = SERVICE_ROOT / "config"


class LlmProfile(BaseModel):
    endpoint: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout: float | None = None
    roles: dict[str, str]
    fallbacks: dict[str, list[str]] = Field(default_factory=dict)


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
                print(
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
                print(
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


def _model_kwargs(profile: LlmProfile) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"temperature": profile.temperature}
    if profile.endpoint:
        kwargs["base_url"] = profile.endpoint
    if profile.max_tokens is not None:
        if profile.endpoint:
            # langchain-openai serializes max_tokens as max_completion_tokens,
            # which OpenAI-compatible backends like Ollama ignore; send the raw
            # field via extra_body so local models are actually capped.
            kwargs["extra_body"] = {"max_tokens": profile.max_tokens}
        else:
            kwargs["max_tokens"] = profile.max_tokens
    if profile.timeout is not None:
        kwargs["timeout"] = profile.timeout
    return kwargs


def _role_models(profile: LlmProfile, role: str) -> list[str]:
    model_name = profile.roles.get(role)
    if not model_name:
        supported = ", ".join(REQUIRED_LLM_ROLES)
        raise ValueError(f"Unsupported LLM role '{role}'. Expected one of: {supported}")
    seen: set[str] = set()
    ordered: list[str] = []
    for item in [model_name, *(profile.fallbacks.get(role) or [])]:
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
    kwargs = _model_kwargs(profile)
    built = [(model_name, init_chat_model(model_name, **kwargs)) for model_name in model_names]
    if len(built) == 1:
        return built[0][1]
    return FallbackChatModel(role, built)
