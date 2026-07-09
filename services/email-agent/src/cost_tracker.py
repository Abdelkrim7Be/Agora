from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Literal
import uuid

import yaml
from langchain_core.callbacks import BaseCallbackHandler

from src.config import SERVICE_ROOT, settings
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
)

DEFAULT_COSTS_PATH = SERVICE_ROOT / "logs" / "llm_costs.jsonl"

# EUR per million tokens. Keep this small and overrideable via costs.yaml because
# provider prices change more often than application code.
PRICES: dict[str, dict[str, float]] = {
    "groq:llama-3.3-70b-versatile": {"in": 0.54, "out": 0.79},
    "llama-3.3-70b-versatile": {"in": 0.54, "out": 0.79},
}


def _path(path: str | Path | None = None) -> Path:
    if path is None:
        path = settings.costs_path or DEFAULT_COSTS_PATH
    p = Path(path)
    return p if p.is_absolute() else SERVICE_ROOT / p


def _load_prices() -> dict[str, dict[str, float]]:
    prices = {model: dict(price) for model, price in PRICES.items()}
    path = SERVICE_ROOT / "costs.yaml"
    if not path.is_file():
        return prices
    data = yaml.safe_load(path.read_text()) or {}
    for model, row in data.get("models", data).items():
        if isinstance(row, dict):
            prices[str(model)] = {
                "in": float(row.get("in", row.get("input", 0.0)) or 0.0),
                "out": float(row.get("out", row.get("output", 0.0)) or 0.0),
            }
    return prices


def selected_cost_backend(path: str | Path | None = None) -> str:
    if path is not None:
        return "json"
    backend = settings.cost_backend.lower().strip()
    if backend not in {"json", "postgres"}:
        raise RuntimeError(f"Unsupported AGENT_COST_BACKEND: {settings.cost_backend}")
    if backend == "postgres" and not settings.database_url:
        raise RuntimeError("DATABASE_URL is required when AGENT_COST_BACKEND=postgres")
    return backend


def _connect():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Postgres cost tracking requires psycopg.") from exc
    return psycopg.connect(settings.database_url)


def setup_cost_tracker() -> None:
    if selected_cost_backend() != "postgres":
        return
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_costs (
                    id BIGSERIAL PRIMARY KEY,
                    event_id TEXT UNIQUE NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL,
                    user_id TEXT NOT NULL,
                    agent_instance_id TEXT NOT NULL,
                    run_id TEXT,
                    node TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    cost_eur DOUBLE PRECISION NOT NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS llm_costs_user_instance_timestamp_idx "
                "ON llm_costs (user_id, agent_instance_id, timestamp DESC)"
            )


def _json_append(entry: dict, path: str | Path | None = None) -> dict:
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def _json_entries(path: str | Path | None = None) -> list[dict]:
    target = _path(path)
    if not target.is_file():
        return []
    entries = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entries.append(json.loads(line))
    return entries


def _pg_insert(entry: dict) -> dict:
    setup_cost_tracker()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO llm_costs (
                    event_id, timestamp, user_id, agent_instance_id, run_id, node, model,
                    input_tokens, output_tokens, total_tokens, cost_eur
                ) VALUES (
                    %(event_id)s, %(timestamp)s, %(user_id)s, %(agent_instance_id)s,
                    %(run_id)s, %(node)s, %(model)s, %(input_tokens)s, %(output_tokens)s,
                    %(total_tokens)s, %(cost_eur)s
                )
                ON CONFLICT (event_id) DO NOTHING
                """,
                entry,
            )
    return entry


def _normalize_entry(entry: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    input_tokens = int(entry.get("input_tokens") or 0)
    output_tokens = int(entry.get("output_tokens") or 0)
    return {
        "event_id": str(entry.get("event_id") or uuid.uuid4()),
        "timestamp": str(entry.get("timestamp") or now),
        "user_id": normalize_user_id(entry.get("user_id") or current_user_id()),
        "agent_instance_id": normalize_agent_instance_id(
            entry.get("agent_instance_id") or current_agent_instance_id()
        ),
        "run_id": str(entry.get("run_id") or ""),
        "node": str(entry.get("node") or "unknown"),
        "model": str(entry.get("model") or "unknown"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(entry.get("total_tokens") or input_tokens + output_tokens),
        "cost_eur": float(entry.get("cost_eur") or 0.0),
    }


def record_cost(entry: dict, path: str | Path | None = None) -> dict:
    normalized = _normalize_entry(entry)
    if selected_cost_backend(path) == "postgres":
        return _pg_insert(normalized)
    return _json_append(normalized, path=path)


def _pg_entries(
    user_id: str | None,
    agent_instance_id: str | None,
    limit: int | None = None,
) -> list[dict]:
    from psycopg.rows import dict_row

    setup_cost_tracker()
    clauses = []
    params: dict[str, Any] = {}
    if user_id is not None:
        clauses.append("user_id = %(user_id)s")
        params["user_id"] = normalize_user_id(user_id)
    if agent_instance_id is not None:
        clauses.append("agent_instance_id = %(agent_instance_id)s")
        params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params["limit"] = 500 if limit is None else max(1, min(limit, 500))
    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT event_id, timestamp, user_id, agent_instance_id, run_id, node, model,
                    input_tokens, output_tokens, total_tokens, cost_eur
                FROM llm_costs
                """
                + where
                + " ORDER BY timestamp DESC LIMIT %(limit)s",
                params,
            )
            rows = []
            for row in cur.fetchall():
                ts = row.get("timestamp")
                rows.append({**row, "timestamp": ts.isoformat(timespec="seconds") if hasattr(ts, "isoformat") else ts})
            return rows


def list_costs(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
    limit: int = 100,
    path: str | Path | None = None,
) -> list[dict]:
    limit = max(1, min(limit, 500))
    if selected_cost_backend(path) == "postgres":
        return _pg_entries(user_id, agent_instance_id, limit=limit)
    entries = _json_entries(path)
    if user_id is not None:
        resolved_user = normalize_user_id(user_id)
        entries = [e for e in entries if normalize_user_id(e.get("user_id")) == resolved_user]
    if agent_instance_id is not None:
        resolved_instance = normalize_agent_instance_id(agent_instance_id)
        entries = [
            e for e in entries
            if normalize_agent_instance_id(e.get("agent_instance_id")) == resolved_instance
        ]
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries[:limit]


def _period_start(period: Literal["session", "day", "month"]) -> datetime | None:
    now = datetime.now(timezone.utc)
    if period == "session":
        return None
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unsupported cost summary period: {period}")


def _parse_timestamp(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def summarize(
    period: Literal["session", "day", "month"],
    user_id: str | None,
    agent_instance_id: str | None,
    path: str | Path | None = None,
) -> dict:
    start = _period_start(period)
    entries = list_costs(
        user_id=user_id,
        agent_instance_id=agent_instance_id,
        limit=500,
        path=path,
    )
    if start is not None:
        entries = [e for e in entries if _parse_timestamp(e["timestamp"]) >= start]

    by_model: dict[str, dict] = defaultdict(lambda: {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_eur": 0.0,
    })
    by_node: dict[str, dict] = defaultdict(lambda: {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_eur": 0.0,
    })
    totals = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_eur": 0.0,
    }
    for entry in entries:
        for bucket in (totals, by_model[entry["model"]], by_node[entry["node"]]):
            bucket["calls"] += 1
            bucket["input_tokens"] += int(entry.get("input_tokens") or 0)
            bucket["output_tokens"] += int(entry.get("output_tokens") or 0)
            bucket["total_tokens"] += int(entry.get("total_tokens") or 0)
            bucket["cost_eur"] += float(entry.get("cost_eur") or 0.0)

    def rounded(rows: dict[str, dict]) -> dict[str, dict]:
        return {k: {**v, "cost_eur": round(v["cost_eur"], 8)} for k, v in rows.items()}

    return {
        "period": period,
        "user_id": normalize_user_id(user_id),
        "agent_instance_id": normalize_agent_instance_id(agent_instance_id),
        "totals": {**totals, "cost_eur": round(totals["cost_eur"], 8)},
        "by_model": rounded(dict(by_model)),
        "by_node": rounded(dict(by_node)),
    }


def _message_from_response(response) -> Any:
    generations = getattr(response, "generations", None) or []
    if generations and generations[0]:
        generation = generations[0][0]
        return getattr(generation, "message", generation)
    return None


def _usage_from_response(response) -> tuple[int, int]:
    message = _message_from_response(response)
    usage = getattr(message, "usage_metadata", None) or {}
    if usage:
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    token_usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
    return (
        int(token_usage.get("input_tokens") or token_usage.get("prompt_tokens") or 0),
        int(token_usage.get("output_tokens") or token_usage.get("completion_tokens") or 0),
    )


def _model_from_response(response) -> str:
    message = _message_from_response(response)
    metadata = getattr(message, "response_metadata", None) or {}
    llm_output = getattr(response, "llm_output", None) or {}
    return str(
        metadata.get("model_name")
        or metadata.get("model")
        or llm_output.get("model_name")
        or llm_output.get("model")
        or "unknown"
    )


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = _load_prices().get(model) or {}
    return round(
        (input_tokens / 1_000_000) * float(price.get("in") or 0.0)
        + (output_tokens / 1_000_000) * float(price.get("out") or 0.0),
        8,
    )


class UsageCallback(BaseCallbackHandler):
    def __init__(
        self,
        *,
        run_id: str = "",
        node: str = "unknown",
        user_id: str | None = None,
        agent_instance_id: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.node = node
        self.user_id = normalize_user_id(user_id or current_user_id())
        self.agent_instance_id = normalize_agent_instance_id(
            agent_instance_id or current_agent_instance_id()
        )

    def on_llm_end(self, response, **kwargs) -> None:
        if not settings.cost_tracking_enabled:
            return
        input_tokens, output_tokens = _usage_from_response(response)
        if input_tokens == 0 and output_tokens == 0:
            return
        metadata = kwargs.get("metadata") or {}
        run_id = str(metadata.get("run_id") or self.run_id)
        node = str(metadata.get("node") or self.node)
        model = _model_from_response(response)
        record_cost({
            "user_id": self.user_id,
            "agent_instance_id": self.agent_instance_id,
            "run_id": run_id,
            "node": node,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_eur": compute_cost(model, input_tokens, output_tokens),
        })


def llm_invoke_config(run_id: str, node: str) -> dict:
    metadata = {"run_id": run_id, "node": node}
    if not settings.cost_tracking_enabled:
        return {"metadata": metadata}
    return {
        "callbacks": [UsageCallback(run_id=run_id, node=node)],
        "metadata": metadata,
    }
