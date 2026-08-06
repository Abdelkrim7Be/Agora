from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml

from platform_core.costs import CostLedger
from platform_core.pricing import DEFAULT_UNKNOWN_MODEL_PRICE, PRICES
from platform_core import pricing
from platform_core.usage import UsageCallback as _UsageCallback

from src.config import SERVICE_ROOT, settings
from src.llm import active_profile_name, load_llm_profile
from src.postgres import tenant_connection
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
)

DEFAULT_COSTS_PATH = SERVICE_ROOT / "logs" / "llm_costs.jsonl"

__all__ = [
    "PRICES",
    "DEFAULT_COSTS_PATH",
    "DEFAULT_UNKNOWN_MODEL_PRICE",
    "selected_cost_backend",
    "setup_cost_tracker",
    "record_cost",
    "list_costs",
    "summarize",
    "totals_for_run_node",
    "count_costs_for_runs",
    "delete_costs_for_runs",
    "compute_cost",
    "UsageCallback",
    "llm_invoke_config",
]


def _path(path: str | Path | None = None) -> Path:
    if path is None:
        path = settings.costs_path or DEFAULT_COSTS_PATH
    p = Path(path)
    return p if p.is_absolute() else SERVICE_ROOT / p


def _profile_price_aliases() -> dict[str, dict[str, float]]:
    """Price the model names this deployment's LLM profile actually asks for.

    A profile can name a model as `provider:model`; the price table may only
    carry the bare name, or vice versa.
    """
    try:
        profile = load_llm_profile(profile_name=active_profile_name())
    except (FileNotFoundError, ValueError, yaml.YAMLError):
        return {}

    aliases: dict[str, dict[str, float]] = {}
    for entry in profile.roles.values():
        # Entries are model strings or per-role config objects with a .model.
        model = entry if isinstance(entry, str) else getattr(entry, "model", "")
        if not model:
            continue
        price = pricing.resolve_price(PRICES, model)
        if price is not None:
            aliases[model] = dict(price)
    return aliases


def _load_prices() -> dict[str, dict[str, float]]:
    prices = {model: dict(price) for model, price in PRICES.items()}
    path = SERVICE_ROOT / "costs.yaml"
    data = yaml.safe_load(path.read_text()) or {} if path.is_file() else {}
    for model, row in data.get("models", data).items():
        if isinstance(row, dict):
            prices[str(model)] = {
                "in": float(row.get("in", row.get("input", 0.0)) or 0.0),
                "out": float(row.get("out", row.get("output", 0.0)) or 0.0),
            }
    for alias, price in _profile_price_aliases().items():
        prices.setdefault(alias, price)
    return prices


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_input_tokens: int = 0,
) -> float:
    return pricing.compute_cost(
        _load_prices(),
        model,
        input_tokens,
        output_tokens,
        cached_input_tokens=cached_input_tokens,
    )


def _connect():
    return tenant_connection()


_ledger = CostLedger(
    resolve_path=_path,
    backend=lambda: settings.cost_backend,
    database_url=lambda: settings.database_url,
    # Resolved at call time so the module-level name stays the patchable seam.
    connect=lambda: _connect(),
    load_prices=_load_prices,
    normalize_user_id=normalize_user_id,
    normalize_agent_instance_id=normalize_agent_instance_id,
    current_user_id=current_user_id,
    current_agent_instance_id=current_agent_instance_id,
)

selected_cost_backend = _ledger.selected_backend
setup_cost_tracker = _ledger.setup
record_cost = _ledger.record
list_costs = _ledger.list
totals_for_run_node = _ledger.totals_for_run_node
count_costs_for_runs = _ledger.count_for_runs
delete_costs_for_runs = _ledger.delete_for_runs


def summarize(
    period: Literal["session", "day", "month"],
    user_id: str | None,
    agent_instance_id: str | None,
    path: str | Path | None = None,
) -> dict:
    return _ledger.summarize(period, user_id, agent_instance_id, path=path)


class UsageCallback(_UsageCallback):
    def __init__(
        self,
        *,
        run_id: str = "",
        node: str = "unknown",
        user_id: str | None = None,
        agent_instance_id: str | None = None,
    ) -> None:
        super().__init__(
            record=record_cost,
            compute_cost=compute_cost,
            enabled=lambda: settings.cost_tracking_enabled,
            run_id=run_id,
            node=node,
            user_id=normalize_user_id(user_id or current_user_id()),
            agent_instance_id=normalize_agent_instance_id(
                agent_instance_id or current_agent_instance_id()
            ),
        )


def llm_invoke_config(run_id: str, node: str) -> dict:
    metadata = {"run_id": run_id, "node": node}
    if not settings.cost_tracking_enabled:
        return {"metadata": metadata}
    return {"callbacks": [UsageCallback(run_id=run_id, node=node)], "metadata": metadata}
