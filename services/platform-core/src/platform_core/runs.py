from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Iterable, Sequence

# Columns every agent's run has, whatever it processes. An agent adds its own on
# top (an email agent contributes subject/author, another would contribute
# something else) and the SQL is generated from the combined list, so a column
# cannot be present in one statement and missing from another.
CORE_COLUMNS = (
    "run_id",
    "user_id",
    "agent_instance_id",
    "status",
    "pending_action",
    "created_at",
    "decision",
    "decision_at",
    "updated_at",
)

# Written straight from the incoming record instead of being coalesced: a null
# here means "cleared", not "unchanged".
OVERWRITE_COLUMNS = frozenset({
    "user_id",
    "agent_instance_id",
    "status",
    "pending_action",
    "updated_at",
})


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunRegistry:
    """Durable index of agent runs and their human-approval state.

    Two backends: a JSON file for local development and Postgres for
    deployments. An explicit path argument always selects the file backend.
    """

    def __init__(
        self,
        *,
        table: str,
        columns: Sequence[str],
        resolve_path: Callable[[str | Path | None], Path],
        backend: Callable[[], str],
        database_url: Callable[[], str],
        connect: Callable[[], Any],
        normalize_user_id: Callable[[str | None], str],
        normalize_agent_instance_id: Callable[[str | None], str],
        current_agent_instance_id: Callable[[], str],
        json_columns: Iterable[str] = (),
        overwrite_columns: Iterable[str] = (),
        max_runs: int = 1000,
        backend_env: str = "AGENT_RUN_REGISTRY_BACKEND",
    ) -> None:
        self.table = table
        self.columns = tuple(columns)
        self.max_runs = max_runs
        self._json_columns = frozenset(json_columns)
        self._overwrite = OVERWRITE_COLUMNS | frozenset(overwrite_columns)
        self._resolve_path = resolve_path
        self._backend = backend
        self._database_url = database_url
        self._connect = connect
        self._normalize_user_id = normalize_user_id
        self._normalize_agent_instance_id = normalize_agent_instance_id
        self._current_agent_instance_id = current_agent_instance_id
        self._backend_env = backend_env
        self._transition_lock = Lock()

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

    def read(self, path: Path) -> dict:
        if not path.is_file():
            return {"runs": []}
        return json.loads(path.read_text())

    def write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    def _json_upsert(self, record: dict, path: str | Path | None = None) -> dict:
        index_path = self._resolve_path(path)
        data = self.read(index_path)
        runs = data.setdefault("runs", [])
        existing = next((r for r in runs if r.get("run_id") == record["run_id"]), None)
        if existing is None:
            runs.append(record)
            saved = record
        else:
            for key, value in record.items():
                if value is not None or key in self._overwrite:
                    existing[key] = value
            if not existing.get("created_at"):
                existing["created_at"] = record.get("created_at") or existing.get("updated_at")
            saved = existing
        runs.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        if len(runs) > self.max_runs:
            data["runs"] = runs[: self.max_runs]
        self.write(index_path, data)
        return saved

    def _json_list(
        self,
        status: str | None,
        path: str | Path | None,
        user_id: str | None,
        agent_instance_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        runs = self.read(self._resolve_path(path)).get("runs", [])
        if user_id is not None:
            wanted = self._normalize_user_id(user_id)
            runs = [r for r in runs if self._normalize_user_id(r.get("user_id")) == wanted]
        if agent_instance_id is not None:
            wanted = self._normalize_agent_instance_id(agent_instance_id)
            runs = [
                r
                for r in runs
                if self._normalize_agent_instance_id(r.get("agent_instance_id")) == wanted
            ]
        if status:
            runs = [r for r in runs if r.get("status") == status]
        if limit is not None:
            return runs[offset : offset + limit]
        return runs

    # -- postgres backend --------------------------------------------------

    @property
    def _column_list(self) -> str:
        return ", ".join(self.columns)

    def _row(self, row: dict[str, Any]) -> dict:
        """Render timestamp columns as ISO strings, whatever the driver returned."""
        created = row.get("created_at")
        decision_at = row.get("decision_at")
        updated = row.get("updated_at")
        if hasattr(created, "isoformat"):
            created = created.isoformat(timespec="seconds")
        if hasattr(decision_at, "isoformat"):
            decision_at = decision_at.isoformat(timespec="seconds")
        if hasattr(updated, "isoformat"):
            updated = updated.isoformat(timespec="seconds")
        return {**row, "created_at": created or updated, "decision_at": decision_at, "updated_at": updated}

    def _upsert_sql(self) -> str:
        columns = self._column_list
        values = ", ".join(f"%({column})s" for column in self.columns)
        updates = []
        for column in self.columns:
            if column == "run_id":
                continue
            if column == "created_at":
                # First write wins: a later upsert must not restamp the run.
                updates.append(f"created_at = COALESCE({self.table}.created_at, EXCLUDED.created_at)")
            elif column in self._overwrite:
                updates.append(f"{column} = EXCLUDED.{column}")
            else:
                updates.append(f"{column} = COALESCE(EXCLUDED.{column}, {self.table}.{column})")
        return (
            f"INSERT INTO {self.table} ({columns}) VALUES ({values})"
            f" ON CONFLICT (run_id) DO UPDATE SET " + ", ".join(updates) +
            f" RETURNING {columns}"
        )

    def _postgres_upsert(self, record: dict) -> dict:
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb

        self.setup()
        params = dict(record)
        for column in self._json_columns:
            value = record.get(column)
            params[column] = Jsonb(value) if value is not None else None
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(self._upsert_sql(), params)
                return self._row(cur.fetchone())

    def _select(self, where: str, params: dict, order: str = "") -> list[dict]:
        from psycopg.rows import dict_row

        self.setup()
        sql = f"SELECT {self._column_list} FROM {self.table}"
        if where:
            sql += f" WHERE {where}"
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql + order, params)
                return [self._row(row) for row in cur.fetchall()]

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

    # -- public api --------------------------------------------------------

    def upsert(self, record: dict, path: str | Path | None = None) -> dict:
        if self.selected_backend(path) == "postgres":
            return self._postgres_upsert(record)
        return self._json_upsert(record, path=path)

    def list(
        self,
        status: str | None = None,
        path: str | Path | None = None,
        user_id: str | None = None,
        agent_instance_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        if self.selected_backend(path) != "postgres":
            return self._json_list(status, path, user_id, agent_instance_id, limit, offset)
        clauses, params = self._tenant_clauses(user_id, agent_instance_id)
        if status:
            clauses.append("status = %(status)s")
            params["status"] = status
        params["limit"] = self.max_runs if limit is None else limit
        params["offset"] = max(offset, 0)
        return self._select(
            " AND ".join(clauses),
            params,
            " ORDER BY updated_at DESC LIMIT %(limit)s OFFSET %(offset)s",
        )

    def get(
        self,
        run_id: str,
        path: str | Path | None = None,
        user_id: str | None = None,
        agent_instance_id: str | None = None,
    ) -> dict | None:
        if self.selected_backend(path) != "postgres":
            runs = self._json_list(None, path, user_id, agent_instance_id)
            return next((r for r in runs if r.get("run_id") == run_id), None)
        clauses, params = self._tenant_clauses(user_id, agent_instance_id)
        clauses = ["run_id = %(run_id)s", *clauses]
        params["run_id"] = run_id
        rows = self._select(" AND ".join(clauses), params)
        return rows[0] if rows else None

    def claim(
        self,
        run_id: str,
        expected_status: str,
        new_status: str,
        path: str | Path | None = None,
        agent_instance_id: str | None = None,
    ) -> dict | None:
        """Atomically move a run between statuses and return the claimed record.

        Returns None when the run was not in `expected_status` — that is how two
        approvers racing on the same run end with only one winner.
        """
        instance_id = self._normalize_agent_instance_id(
            agent_instance_id or self._current_agent_instance_id()
        )
        if self.selected_backend(path) == "postgres":
            return self._postgres_update(
                "status = %(new_status)s, updated_at = NOW()",
                [
                    "run_id = %(run_id)s",
                    "agent_instance_id = %(agent_instance_id)s",
                    "status = %(expected_status)s",
                ],
                {
                    "run_id": run_id,
                    "agent_instance_id": instance_id,
                    "expected_status": expected_status,
                    "new_status": new_status,
                },
            )
        return self._json_update(
            path,
            run_id,
            instance_id,
            {"status": new_status},
            expected_status=expected_status,
        )

    def assign(
        self,
        run_id: str,
        assignee: str | None,
        path: str | Path | None = None,
        agent_instance_id: str | None = None,
    ) -> dict | None:
        instance_id = self._normalize_agent_instance_id(
            agent_instance_id or self._current_agent_instance_id()
        )
        if self.selected_backend(path) == "postgres":
            return self._postgres_update(
                "assignee = %(assignee)s, updated_at = NOW()",
                ["run_id = %(run_id)s", "agent_instance_id = %(agent_instance_id)s"],
                {"run_id": run_id, "agent_instance_id": instance_id, "assignee": assignee},
            )
        return self._json_update(path, run_id, instance_id, {"assignee": assignee})

    def _postgres_update(self, assignment: str, clauses: list[str], params: dict) -> dict | None:
        from psycopg.rows import dict_row

        self.setup()
        sql = (
            f"UPDATE {self.table} SET {assignment} WHERE " + " AND ".join(clauses) +
            f" RETURNING {self._column_list}"
        )
        with self._connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return self._row(row) if row else None

    def _json_update(
        self,
        path: str | Path | None,
        run_id: str,
        instance_id: str,
        changes: dict,
        expected_status: str | None = None,
    ) -> dict | None:
        with self._transition_lock:
            index_path = self._resolve_path(path)
            data = self.read(index_path)
            record = next(
                (
                    item
                    for item in data.get("runs", [])
                    if item.get("run_id") == run_id
                    and self._normalize_agent_instance_id(item.get("agent_instance_id"))
                    == instance_id
                    and (expected_status is None or item.get("status") == expected_status)
                ),
                None,
            )
            if record is None:
                return None
            record.update(changes)
            record["updated_at"] = _now()
            self.write(index_path, data)
            return record.copy()

    def list_before(
        self,
        cutoff: datetime,
        path: str | Path | None = None,
        agent_instance_id: str | None = None,
    ) -> list[dict]:
        cutoff = cutoff.astimezone(timezone.utc)
        if self.selected_backend(path) == "postgres":
            clauses = ["COALESCE(created_at, updated_at) < %(cutoff)s"]
            params: dict[str, Any] = {"cutoff": cutoff}
            if agent_instance_id is not None:
                clauses.append("agent_instance_id = %(agent_instance_id)s")
                params["agent_instance_id"] = self._normalize_agent_instance_id(agent_instance_id)
            return self._select(
                " AND ".join(clauses), params, " ORDER BY COALESCE(created_at, updated_at) ASC"
            )
        selected = [
            record
            for record in self._json_list(None, path, None, agent_instance_id)
            if (stamp := parse_timestamp(record.get("created_at") or record.get("updated_at")))
            is not None
            and stamp < cutoff
        ]
        selected.sort(key=lambda item: item.get("created_at") or item.get("updated_at") or "")
        return selected

    def delete(
        self,
        run_ids: list[str],
        path: str | Path | None = None,
        agent_instance_id: str | None = None,
    ) -> int:
        run_ids = [str(run_id) for run_id in run_ids if run_id]
        if not run_ids:
            return 0
        if self.selected_backend(path) == "postgres":
            self.setup()
            clauses = ["run_id = ANY(%(run_ids)s)"]
            params: dict[str, Any] = {"run_ids": run_ids}
            if agent_instance_id is not None:
                clauses.append("agent_instance_id = %(agent_instance_id)s")
                params["agent_instance_id"] = self._normalize_agent_instance_id(agent_instance_id)
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"DELETE FROM {self.table} WHERE " + " AND ".join(clauses), params
                    )
                    return cur.rowcount or 0
        index_path = self._resolve_path(path)
        data = self.read(index_path)
        kept = []
        deleted = 0
        for record in data.get("runs", []):
            matches_instance = agent_instance_id is None or self._normalize_agent_instance_id(
                record.get("agent_instance_id")
            ) == self._normalize_agent_instance_id(agent_instance_id)
            if record.get("run_id") in run_ids and matches_instance:
                deleted += 1
            else:
                kept.append(record)
        data["runs"] = kept
        self.write(index_path, data)
        return deleted
