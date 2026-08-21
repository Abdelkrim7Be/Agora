"""Token accounting for the quarantine model.

This service runs a model on every inbound message, and none of that was
counted anywhere: the cost dashboard showed only the agent's own calls, so the
platform under-reported its real model usage — and the classifier turned out to
be the heaviest consumer of the two.

`with_structured_output` returns a parsed object, not a message, so the usage
metadata is gone by the time the caller sees the verdict. A callback observes
the raw LLM result before that happens.
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


class UsageCollector(BaseCallbackHandler):
    """Accumulates token counts across however many calls one request makes."""

    def __init__(self) -> None:
        self.model = ""
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:  # pragma: no cover - exercised via sanitize
        self.calls += 1
        message = _first_message(response)
        usage = getattr(message, "usage_metadata", None) or {}
        if usage:
            self.input_tokens += int(usage.get("input_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or 0)
        else:
            token_usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
            self.input_tokens += int(
                token_usage.get("input_tokens") or token_usage.get("prompt_tokens") or 0
            )
            self.output_tokens += int(
                token_usage.get("output_tokens") or token_usage.get("completion_tokens") or 0
            )
        if not self.model:
            self.model = _model_name(response, message)

    def as_dict(self) -> dict:
        return {
            "model": self.model or "unknown",
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
        }

    @property
    def recorded_anything(self) -> bool:
        return self.calls > 0

    def config(self) -> dict:
        """Invoke config that routes results through this collector."""
        return {"callbacks": [self]}

    def invoke(self, llm, messages):
        """Call `llm` so usage is captured when it can be, never at the cost of
        the call itself.

        Not every client accepts a `config` argument — a stub in a test, or any
        object standing in for the model, may take the messages alone. Passing
        one unconditionally turned those into a TypeError that the caller reads
        as "the classifier is unavailable", i.e. an accounting detail silently
        changing a security verdict.
        """
        try:
            return llm.invoke(messages, config=self.config())
        except TypeError:
            return llm.invoke(messages)


def _first_message(response: Any) -> Any:
    generations = getattr(response, "generations", None) or []
    if generations and generations[0]:
        generation = generations[0][0]
        return getattr(generation, "message", generation)
    return None


def _model_name(response: Any, message: Any) -> str:
    metadata = getattr(message, "response_metadata", None) or {}
    llm_output = getattr(response, "llm_output", None) or {}
    return str(
        metadata.get("model_name")
        or metadata.get("model")
        or llm_output.get("model_name")
        or llm_output.get("model")
        or "unknown"
    )
