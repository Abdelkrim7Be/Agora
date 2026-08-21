from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# EUR per million tokens. Keep this small and overrideable from a service's own
# costs.yaml, because provider prices change more often than application code.
PRICES: dict[str, dict[str, float]] = {
    "groq:llama-3.3-70b-versatile": {"in": 0.54, "out": 0.79},
    "llama-3.3-70b-versatile": {"in": 0.54, "out": 0.79},
    # Mistral API public list prices per million tokens, checked 2026-08-06.
    "mistral/mistral-small-latest": {"in": 0.15, "out": 0.60},
    "mistral/mistral-small-2603": {"in": 0.15, "out": 0.60},
    "mistral-small-latest": {"in": 0.15, "out": 0.60},
    "mistral-small-2603": {"in": 0.15, "out": 0.60},
    "mistral/mistral-large-latest": {"in": 0.50, "out": 1.50},
    "mistral/mistral-large-2512": {"in": 0.50, "out": 1.50},
    "mistral-large-latest": {"in": 0.50, "out": 1.50},
    "mistral-large-2512": {"in": 0.50, "out": 1.50},
    # Prod LiteLLM logical routes. These keep costs nonzero even when the
    # OpenAI-compatible client reports the proxy model name rather than the
    # underlying provider model id.
    "agora-triage": {"in": 0.15, "out": 0.60},
    "agora-quarantine": {"in": 0.15, "out": 0.60},
    "agora-memory-style": {"in": 0.15, "out": 0.60},
    "agora-draft": {"in": 0.50, "out": 1.50},
    "agora-reason": {"in": 0.50, "out": 1.50},
    # Local Ollama models: estimated compute cost (electricity/amortization),
    # not a provider invoice — keeps the cost dashboard meaningful locally.
    # Every locally served model needs a row here: an unpriced one silently
    # books at zero, which is how the costs page read "EUR 0.000000" while the
    # GPU was busy for minutes at a time.
    "qwen2.5:3b-8k": {"in": 0.02, "out": 0.06},
    "qwen2.5:3b": {"in": 0.02, "out": 0.06},
    # 4B params against 3B: proportionally more compute per token.
    "qwen3:4b-4k": {"in": 0.03, "out": 0.08},
    "qwen3:4b-8k": {"in": 0.03, "out": 0.08},
    "qwen3:4b-instruct": {"in": 0.03, "out": 0.08},
    "qwen3:4b": {"in": 0.03, "out": 0.08},
}

DEFAULT_UNKNOWN_MODEL_PRICE = {"in": 0.0, "out": 0.0}

# Providers that bill a cached prompt prefix at a fraction of the input price.
CACHED_PREFIX_MULTIPLIER = 0.1


def resolve_price(prices: dict[str, dict[str, float]], model: str) -> dict[str, float] | None:
    """Look a model up, falling back to the bare name after a provider prefix."""
    price = prices.get(model)
    if price is None and ":" in model:
        price = prices.get(model.split(":", 1)[1])
    return price


def is_priced(prices: dict[str, dict[str, float]], model: str) -> bool:
    return resolve_price(prices, model) is not None


def _cached_multiplier(model: str) -> float:
    normalized = model.lower().split(":", 1)[-1]
    if "mistral" in normalized or normalized.startswith("agora-"):
        return CACHED_PREFIX_MULTIPLIER
    return 1.0


def compute_cost(
    prices: dict[str, dict[str, float]],
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_input_tokens: int = 0,
) -> float:
    price = resolve_price(prices, model)
    if price is None:
        logger.warning("No pricing configured for model '%s'; defaulting to zero cost.", model)
        price = DEFAULT_UNKNOWN_MODEL_PRICE
    # A provider reporting more cached tokens than input tokens would otherwise
    # produce a negative billable count.
    cached_input_tokens = max(0, min(int(cached_input_tokens or 0), int(input_tokens or 0)))
    billable_input_tokens = int(input_tokens or 0) - cached_input_tokens
    price_in = float(price.get("in") or 0.0)
    return round(
        (billable_input_tokens / 1_000_000) * price_in
        + (cached_input_tokens / 1_000_000) * price_in * _cached_multiplier(model)
        + (output_tokens / 1_000_000) * float(price.get("out") or 0.0),
        8,
    )
