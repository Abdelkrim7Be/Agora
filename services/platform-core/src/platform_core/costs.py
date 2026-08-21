from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Literal
import uuid

MAX_ROWS = 500
ZERO_TOTALS = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_eur": 0.0}


def period_start(period: Literal["session", "day", "month"]) -> datetime | None:
    now = datetime.now(timezone.utc)
    if period == "session":
        return None
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unsupported cost summary period: {period}")


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _empty_bucket() -> dict:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_eur": 0.0}


class CostLedger:
    """Append-only record of what every model call cost, per tenant.

    Two backends: a JSONL file for local development and Postgres for
    deployments. An explicit path argument always selects the file backend, so a
    test can point at a temp file without reconfiguring the service.
    """

    def __init__(
        self,
        *,
        resolve_path: Callable[[str | Path | None], Path],
        backend: Callable[[], str],
        database_url: Callable[[], str],
        connect: Callable[[], Any],
        load_prices: Callable[[], dict[str, dict[str, float]]],
        normalize_user_id: Callable[[str | None], str],
        normalize_agent_instance_id: Callable[[str | None], str],
        current_user_id: Callable[[], str],
        current_agent_instance_id: Callable[[], str],
        backend_env: str = "AGENT_COST_BACKEND",
    ) -> None:
        self._resolve_path = resolve_path
        self._backend = backend
        self._database_url = database_url
        self._connect = connect
        self._load_prices = load_prices
        self._normalize_user_id = normalize_user_id
        self._normalize_agent_instance_id = normalize_agent_instance_id
        self._current_user_id = current_user_id
        self._current_agent_instance_id = current_agent_instance_id
        self._backend_env = backend_env

    # -- backend selection -------------------------------------------------

    def selected_backend(self, path: str | Path | None = None) -> str:
        if path is not None:
            return "json"
        backend = self._backend().lower().strip()
        if backend not in {"json", "postgres"}:
            raise RuntimeError(f"Unsupported {self._backend_env}: {self._backend()}")
        if backend == "postgres" and not self._database_url():
            raise RuntimeError(f"DATABASE_URL is required when {self._backend_env}=postgres")
        return backend

    def setup(self) -> None:
        # Postgres schema is owned by migrations; the JSON path needs nothing.
        return

    # -- json backend ------------------------------------------------------

    def _json_append(self, entry: dict, path: str | Path | None = None) -> dict:
        target = self._resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def json_entries(self, path: str | Path | None = None) -> list[dict]:
        target = self._resolve_path(path)
        if not target.is_file():
            return []
        return [
            json.loads(line)
            for line in target.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    # -- writes ------------------------------------------------------------

    def normalize_entry(self, entry: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        input_tokens = int(entry.get("input_tokens") or 0)
        output_tokens = int(entry.get("output_tokens") or 0)
        return {
            "event_id": str(entry.get("event_id") or uuid.uuid4()),
            "timestamp": str(entry.get("timestamp") or now),
            "user_id": self._normalize_user_id(entry.get("user_id") or self._current_user_id()),
            "agent_instance_id": self._normalize_agent_instance_id(
                entry.get("agent_instance_id") or self._current_agent_instance_id()
            ),
            "run_id": str(entry.get("run_id") or ""),
            "node": str(entry.get("node") or "unknown"),
            "model": str(entry.get("model") or "unknown"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": int(entry.get("total_tokens") or input_tokens + output_tokens),
            "cost_eur": float(entry.get("cost_eur") or 0.0),
        }

    def record(self, entry: dict, path: str | Path | None = None) -> dict:
        normalized = self.normalize_entry(entry)
        if self.selected_backend(path) == "postgres":
            return self._pg_insert(normalized)
        return self._json_append(normalized, path=path)

    def _pg_insert(self, entry: dict) -> dict:
        self.setup()
        with self._connect() as conn:
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

    # -- reads -------------------------------------------------------------

    def _tenant_clauses(
        self, user_id: str | None, agent_instance_id: str | None
    ) -> tuple[list[str], dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if user_id is not None:
            clauses.append("user_id = %(user_id)s")
            params["user_id"] = self._normalize_user_id(user_id)
        if agent_instance_id is not None:
            clauses.append("agent_instance_id = %(agent_instance_id)s")
            params["agent_instance_id"] = self._normalize_agent_instance_id(agent_instance_id)
        return clauses, params

    def _pg_entries(
        self,
        user_id: str | None,
        agent_instance_id: str | None,
        limit: int | None = None,
    ) -> list[dict]:
        from psycopg.rows import dict_row

        self.setup()
        clauses, params = self._tenant_clauses(user_id, agent_instance_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params["limit"] = MAX_ROWS if limit is None else max(1, min(limit, MAX_ROWS))
        with self._connect() as conn:
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
                    rows.append({
                        **row,
                        "timestamp": ts.isoformat(timespec="seconds")
                        if hasattr(ts, "isoformat")
                        else ts,
                    })
                return rows

    def list(
        self,
        user_id: str | None = None,
        agent_instance_id: str | None = None,
        limit: int = 100,
        path: str | Path | None = None,
    ) -> list[dict]:
        limit = max(1, min(limit, MAX_ROWS))
        if self.selected_backend(path) == "postgres":
            return self._pg_entries(user_id, agent_instance_id, limit=limit)
        entries = self.json_entries(path)
        if user_id is not None:
            wanted = self._normalize_user_id(user_id)
            entries = [e for e in entries if self._normalize_user_id(e.get("user_id")) == wanted]
        if agent_instance_id is not None:
            wanted = self._normalize_agent_instance_id(agent_instance_id)
            entries = [
                e
                for e in entries
                if self._normalize_agent_instance_id(e.get("agent_instance_id")) == wanted
            ]
        entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return entries[:limit]

    def summarize(
        self,
        period: Literal["session", "day", "month"],
        user_id: str | None,
        agent_instance_id: str | None,
        path: str | Path | None = None,
    ) -> dict:
        start = period_start(period)
        entries = self.list(
            user_id=user_id, agent_instance_id=agent_instance_id, limit=MAX_ROWS, path=path
        )
        if start is not None:
            entries = [e for e in entries if parse_timestamp(e["timestamp"]) >= start]

        by_model: dict[str, dict] = defaultdict(_empty_bucket)
        by_node: dict[str, dict] = defaultdict(_empty_bucket)
        totals = _empty_bucket()
        for entry in entries:
            for bucket in (totals, by_model[entry["model"]], by_node[entry["node"]]):
                bucket["calls"] += 1
                bucket["input_tokens"] += int(entry.get("input_tokens") or 0)
                bucket["output_tokens"] += int(entry.get("output_tokens") or 0)
                bucket["total_tokens"] += int(entry.get("total_tokens") or 0)
                bucket["cost_eur"] += float(entry.get("cost_eur") or 0.0)

        def rounded(rows: dict[str, dict]) -> dict[str, dict]:
            return {k: {**v, "cost_eur": round(v["cost_eur"], 8)} for k, v in rows.items()}

        # A model with no price row books at zero, and a zero total is
        # indistinguishable from "nothing ran". Name them so the view can say the
        # estimate is incomplete rather than implying the work was free.
        prices = self._load_prices()
        unpriced = sorted(
            model
            for model in by_model
            if model not in prices and model.split(":", 1)[-1] not in prices
        )

        return {
            "period": period,
            "user_id": self._normalize_user_id(user_id),
            "agent_instance_id": self._normalize_agent_instance_id(agent_instance_id),
            "totals": {**totals, "cost_eur": round(totals["cost_eur"], 8)},
            "by_model": rounded(dict(by_model)),
            "by_node": rounded(dict(by_node)),
            "unpriced_models": unpriced,
        }

    def totals_for_run_node(
        self,
        run_id: str,
        node: str,
        user_id: str | None = None,
        agent_instance_id: str | None = None,
        path: str | Path | None = None,
    ) -> dict:
        if not run_id:
            return dict(ZERO_TOTALS)
        if self.selected_backend(path) == "postgres":
            clauses, params = self._tenant_clauses(user_id, agent_instance_id)
            clauses = ["run_id = %(run_id)s", "node = %(node)s", *clauses]
            params.update({"run_id": run_id, "node": node})
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0),"
                        " COALESCE(SUM(total_tokens), 0), COALESCE(SUM(cost_eur), 0)"
                        " FROM llm_costs WHERE " + " AND ".join(clauses),
                        params,
                    )
                    row = cur.fetchone() or (0, 0, 0, 0.0)
            return {
                "input_tokens": int(row[0] or 0),
                "output_tokens": int(row[1] or 0),
                "total_tokens": int(row[2] or 0),
                "cost_eur": float(row[3] or 0.0),
            }
        totals = dict(ZERO_TOTALS)
        for entry in self.json_entries(path):
            if entry.get("run_id") != run_id or entry.get("node") != node:
                continue
            if not self._entry_matches(entry, user_id, agent_instance_id):
                continue
            totals["input_tokens"] += int(entry.get("input_tokens") or 0)
            totals["output_tokens"] += int(entry.get("output_tokens") or 0)
            totals["total_tokens"] += int(entry.get("total_tokens") or 0)
            totals["cost_eur"] += float(entry.get("cost_eur") or 0.0)
        totals["cost_eur"] = round(totals["cost_eur"], 8)
        return totals

    def _entry_matches(
        self, entry: dict, user_id: str | None, agent_instance_id: str | None
    ) -> bool:
        if user_id is not None and self._normalize_user_id(
            entry.get("user_id")
        ) != self._normalize_user_id(user_id):
            return False
        if agent_instance_id is not None and self._normalize_agent_instance_id(
            entry.get("agent_instance_id")
        ) != self._normalize_agent_instance_id(agent_instance_id):
            return False
        return True

    # -- erasure -----------------------------------------------------------

    def _run_clauses(
        self, run_ids: list[str], agent_instance_id: str | None
    ) -> tuple[list[str], dict[str, Any]]:
        clauses = ["run_id = ANY(%(run_ids)s)"]
        params: dict[str, Any] = {"run_ids": run_ids}
        if agent_instance_id is not None:
            clauses.append("agent_instance_id = %(agent_instance_id)s")
            params["agent_instance_id"] = self._normalize_agent_instance_id(agent_instance_id)
        return clauses, params

    def count_for_runs(
        self,
        run_ids: list[str],
        agent_instance_id: str | None = None,
        path: str | Path | None = None,
    ) -> int:
        run_ids = [str(run_id) for run_id in run_ids if run_id]
        if not run_ids:
            return 0
        if self.selected_backend(path) == "postgres":
            clauses, params = self._run_clauses(run_ids, agent_instance_id)
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM llm_costs WHERE " + " AND ".join(clauses), params
                    )
                    row = cur.fetchone()
                    return int(row[0] if row else 0)
        return sum(
            1
            for entry in self.json_entries(path)
            if entry.get("run_id") in run_ids
            and self._entry_matches(entry, None, agent_instance_id)
        )

    def delete_for_runs(
        self,
        run_ids: list[str],
        agent_instance_id: str | None = None,
        path: str | Path | None = None,
    ) -> int:
        run_ids = [str(run_id) for run_id in run_ids if run_id]
        if not run_ids:
            return 0
        if self.selected_backend(path) == "postgres":
            clauses, params = self._run_clauses(run_ids, agent_instance_id)
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM llm_costs WHERE " + " AND ".join(clauses), params
                    )
                    return cur.rowcount or 0
        target = self._resolve_path(path)
        if not target.is_file():
            return 0
        kept: list[dict] = []
        deleted = 0
        for entry in self.json_entries(path):
            matches = entry.get("run_id") in run_ids and self._entry_matches(
                entry, None, agent_instance_id
            )
            if matches:
                deleted += 1
            else:
                kept.append(entry)
        target.write_text(
            "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in kept), encoding="utf-8"
        )
        return deleted
