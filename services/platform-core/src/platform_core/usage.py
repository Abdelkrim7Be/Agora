from __future__ import annotations

from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler


def message_from_response(response: Any) -> Any:
    generations = getattr(response, "generations", None) or []
    if generations and generations[0]:
        generation = generations[0][0]
        return getattr(generation, "message", generation)
    return None


def cached_input_tokens(response: Any) -> int:
    """Read the cached-prefix token count, whichever name the provider used."""
    message = message_from_response(response)
    usage = getattr(message, "usage_metadata", None) or {}
    details = usage.get("input_token_details") if isinstance(usage, dict) else None
    if isinstance(details, dict):
        for key in ("cached_tokens", "cache_read", "cache_read_tokens"):
            if details.get(key):
                return int(details[key])
    token_usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
    prompt_details = (
        token_usage.get("prompt_tokens_details") or token_usage.get("input_tokens_details") or {}
    )
    if isinstance(prompt_details, dict):
        return int(prompt_details.get("cached_tokens") or prompt_details.get("cache_read") or 0)
    return 0


def usage_from_response(response: Any) -> tuple[int, int, int]:
    """Return (input, output, cached input) tokens for one model response."""
    message = message_from_response(response)
    usage = getattr(message, "usage_metadata", None) or {}
    if usage:
        return (
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            cached_input_tokens(response),
        )
    token_usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
    return (
        int(token_usage.get("input_tokens") or token_usage.get("prompt_tokens") or 0),
        int(token_usage.get("output_tokens") or token_usage.get("completion_tokens") or 0),
        cached_input_tokens(response),
    )


def model_from_response(response: Any) -> str:
    message = message_from_response(response)
    metadata = getattr(message, "response_metadata", None) or {}
    llm_output = getattr(response, "llm_output", None) or {}
    return str(
        metadata.get("model_name")
        or metadata.get("model")
        or llm_output.get("model_name")
        or llm_output.get("model")
        or "unknown"
    )


class UsageCallback(BaseCallbackHandler):
    """Books one ledger entry per model response.

    The run id and node are captured at construction but a per-call metadata
    override wins, so a shared callback still attributes work to the node that
    actually ran.
    """

    def __init__(
        self,
        *,
        record: Callable[[dict], Any],
        compute_cost: Callable[..., float],
        enabled: Callable[[], bool],
        run_id: str = "",
        node: str = "unknown",
        user_id: str = "",
        agent_instance_id: str = "",
    ) -> None:
        self._record = record
        self._compute_cost = compute_cost
        self._enabled = enabled
        self.run_id = run_id
        self.node = node
        self.user_id = user_id
        self.agent_instance_id = agent_instance_id

    def on_llm_end(self, response, **kwargs) -> None:
        if not self._enabled():
            return
        input_tokens, output_tokens, cached = usage_from_response(response)
        if input_tokens == 0 and output_tokens == 0:
            return
        metadata = kwargs.get("metadata") or {}
        model = model_from_response(response)
        self._record({
            "user_id": self.user_id,
            "agent_instance_id": self.agent_instance_id,
            "run_id": str(metadata.get("run_id") or self.run_id),
            "node": str(metadata.get("node") or self.node),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_eur": self._compute_cost(
                model, input_tokens, output_tokens, cached_input_tokens=cached
            ),
        })
