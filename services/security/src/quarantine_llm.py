from __future__ import annotations

import os
from typing import Any

from langchain.chat_models import init_chat_model

from src.config import settings
from src.models import QuarantineVerdict, TrustClassificationVerdict


class ToollessQuarantineClient:
    """Quarantine LLM boundary: structured reads only, never tool-calling."""

    def __init__(self, candidate: Any):
        self._candidate = candidate
        self.model_name = getattr(candidate, "model_name", settings.sanitize_model)

    def __getattr__(self, item: str):
        return getattr(self._candidate, item)

    def invoke(self, messages, config=None):
        try:
            return self._candidate.invoke(messages, config=config)
        except TypeError as exc:
            if config is None or "config" not in str(exc):
                raise
            return self._candidate.invoke(messages)

    def bind_tools(self, *args, **kwargs):
        raise RuntimeError("quarantine LLM clients cannot bind tools")

    def with_structured_output(self, *args, **kwargs):
        return ToollessQuarantineClient(
            self._candidate.with_structured_output(*args, **kwargs)
        )


def quarantine_model_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {"temperature": 0.0}
    if settings.sanitize_endpoint:
        kwargs["base_url"] = settings.sanitize_endpoint
        kwargs["api_key"] = os.getenv("OPENAI_API_KEY") or "ollama-local"
    if settings.sanitize_timeout is not None:
        kwargs["timeout"] = settings.sanitize_timeout
    return kwargs


def build_quarantine_client() -> ToollessQuarantineClient:
    return ToollessQuarantineClient(
        init_chat_model(settings.sanitize_model, **quarantine_model_kwargs())
    )


def build_quarantine_classifier() -> ToollessQuarantineClient:
    return build_quarantine_client().with_structured_output(QuarantineVerdict)


def build_trust_classifier() -> ToollessQuarantineClient:
    return build_quarantine_client().with_structured_output(TrustClassificationVerdict)
