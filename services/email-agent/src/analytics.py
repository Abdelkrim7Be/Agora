from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from src.run_registry import _connect, _path, _postgres_row, _read, selected_run_registry_backend
from src.tenant import normalize_agent_instance_id, normalize_user_id

AnalyticsPeriod = Literal["day", "week", "month"]
_BUCKET_WIDTH = {"day": timedelta(hours=1), "week": timedelta(days=1), "month": timedelta(days=1)}


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _period_start(period: AnalyticsPeriod) -> datetime:
    now = datetime.now(timezone.utc)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return (now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6))
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unsupported analytics period: {period}")


def _bucket_start(moment: datetime, period: AnalyticsPeriod) -> datetime:
    if period == "day":
        return moment.replace(minute=0, second=0, microsecond=0)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _bucket_label(moment: datetime, period: AnalyticsPeriod) -> str:
    local = moment.astimezone(timezone.utc)
    if period == "day":
        return local.strftime("%H:00")
    return local.strftime("%d/%m")


def _timeline_seed(period: AnalyticsPeriod, start: datetime) -> list[datetime]:
    points: list[datetime] = []
    cursor = start
    step = _BUCKET_WIDTH[period]
    now = datetime.now(timezone.utc)
    while cursor <= now:
        points.append(cursor)
        cursor += step
    return points


def _normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    created_at = record.get("created_at") or record.get("updated_at")
    return {
        **record,
        "created_at": created_at,
        "workflow_dept": record.get("workflow_dept"),
        "decision": record.get("decision"),
        "decision_at": record.get("decision_at"),
    }


def _json_runs(user_id: str | None, agent_instance_id: str | None) -> list[dict[str, Any]]:
    runs = _read(_path()).get("runs", [])
    if user_id is not None:
        resolved_user_id = normalize_user_id(user_id)
        runs = [r for r in runs if normalize_user_id(r.get("user_id")) == resolved_user_id]
    if agent_instance_id is not None:
        resolved_instance_id = normalize_agent_instance_id(agent_instance_id)
        runs = [
            r for r in runs
            if normalize_agent_instance_id(r.get("agent_instance_id")) == resolved_instance_id
        ]
    return [_normalized_record(r) for r in runs]


def _postgres_runs(user_id: str | None, agent_instance_id: str | None) -> list[dict[str, Any]]:
    from psycopg.rows import dict_row

    clauses = []
    params: dict[str, Any] = {}
    if user_id is not None:
        clauses.append("user_id = %(user_id)s")
        params["user_id"] = normalize_user_id(user_id)
    if agent_instance_id is not None:
        clauses.append("agent_instance_id = %(agent_instance_id)s")
        params["agent_instance_id"] = normalize_agent_instance_id(agent_instance_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT run_id, user_id, agent_instance_id, status, classification,
                    pending_action, subject, author, email_id, gmail_thread_id,
                    category, category_display_name, priority, template, workflow_owner,
                    workflow_approver, workflow_route_to, workflow_dept, assignee,
                    created_at, decision, decision_at, error, updated_at
                FROM agent_runs
                """
                + where,
                params,
            )
            return [_normalized_record(_postgres_row(row)) for row in cur.fetchall()]


def _load_runs(user_id: str | None, agent_instance_id: str | None) -> list[dict[str, Any]]:
    if selected_run_registry_backend() == "postgres":
        return _postgres_runs(user_id, agent_instance_id)
    return _json_runs(user_id, agent_instance_id)


def summarize(
    period: AnalyticsPeriod,
    *,
    user_id: str | None,
    agent_instance_id: str | None,
    workflow_dept: str | None = None,
) -> dict[str, Any]:
    start = _period_start(period)
    seeded_points = _timeline_seed(period, start)
    runs = _load_runs(user_id, agent_instance_id)
    if workflow_dept:
        runs = [
            r for r in runs
            if not r.get("workflow_dept") or str(r.get("workflow_dept")).strip() == workflow_dept
        ]

    filtered: list[dict[str, Any]] = []
    for record in runs:
        created_at = _parse_timestamp(record.get("created_at"))
        if created_at is None or created_at < start:
            continue
        filtered.append({**record, "_created_at": created_at})

    timeline_counts = {point: 0 for point in seeded_points}
    status_breakdown: Counter[str] = Counter()
    decision_breakdown: Counter[str] = Counter()
    workflow_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "category": "uncategorized",
        "display_name": "Sans workflow",
        "count": 0,
        "pending": 0,
        "approved": 0,
        "rejected": 0,
        "turnaround_total_seconds": 0.0,
        "turnaround_count": 0,
    })
    dept_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "dept": "Sans departement",
        "count": 0,
        "pending": 0,
        "approved": 0,
        "rejected": 0,
    })
    turnaround_values: list[float] = []

    for record in filtered:
        created_at = record["_created_at"]
        bucket = _bucket_start(created_at, period)
        if bucket in timeline_counts:
            timeline_counts[bucket] += 1
        status = str(record.get("status") or "unknown")
        status_breakdown[status] += 1

        category_key = str(record.get("category") or "uncategorized")
        display_name = str(record.get("category_display_name") or category_key or "uncategorized")
        workflow = workflow_rows[category_key]
        workflow["category"] = category_key
        workflow["display_name"] = display_name
        workflow["count"] += 1
        if status == "pending_approval":
            workflow["pending"] += 1

        dept_name = str(record.get("workflow_dept") or "Sans departement")
        dept = dept_rows[dept_name]
        dept["dept"] = dept_name
        dept["count"] += 1
        if status == "pending_approval":
            dept["pending"] += 1

        decision = record.get("decision")
        if decision in {"approved", "rejected"}:
            decision_key = str(decision)
            decision_breakdown[decision_key] += 1
            workflow[decision_key] += 1
            dept[decision_key] += 1
            decision_at = _parse_timestamp(record.get("decision_at"))
            if decision_at is not None and decision_at >= created_at:
                turnaround = (decision_at - created_at).total_seconds()
                turnaround_values.append(turnaround)
                workflow["turnaround_total_seconds"] += turnaround
                workflow["turnaround_count"] += 1

    workflow_list = []
    for row in workflow_rows.values():
        avg_turnaround = None
        if row["turnaround_count"]:
            avg_turnaround = round(row["turnaround_total_seconds"] / row["turnaround_count"], 2)
        workflow_list.append({
            "category": row["category"],
            "display_name": row["display_name"],
            "count": row["count"],
            "pending": row["pending"],
            "approved": row["approved"],
            "rejected": row["rejected"],
            "avg_turnaround_seconds": avg_turnaround,
        })
    workflow_list.sort(key=lambda row: (-row["count"], row["display_name"]))

    dept_list = list(dept_rows.values())
    dept_list.sort(key=lambda row: (-row["count"], row["dept"]))

    decided_total = decision_breakdown["approved"] + decision_breakdown["rejected"]
    approval_rate_pct = round((decision_breakdown["approved"] / decided_total) * 100, 2) if decided_total else None
    avg_turnaround_seconds = round(sum(turnaround_values) / len(turnaround_values), 2) if turnaround_values else None

    return {
        "period": period,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_id": normalize_user_id(user_id),
        "agent_instance_id": normalize_agent_instance_id(agent_instance_id),
        "workflow_dept": workflow_dept,
        "totals": {
            "emails_handled": len(filtered),
            "pending": status_breakdown["pending_approval"],
            "approved": decision_breakdown["approved"],
            "rejected": decision_breakdown["rejected"],
            "approval_rate_pct": approval_rate_pct,
            "avg_turnaround_seconds": avg_turnaround_seconds,
            "decided_count": decided_total,
        },
        "status_breakdown": dict(status_breakdown),
        "volume_timeline": [
            {"bucket_start": point.isoformat(timespec="seconds"), "label": _bucket_label(point, period), "count": timeline_counts[point]}
            for point in seeded_points
        ],
        "by_workflow": workflow_list,
        "top_categories": workflow_list[:5],
        "dept_load": dept_list,
    }
