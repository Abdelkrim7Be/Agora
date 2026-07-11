from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src import analytics
from src import api
from src.api import app
from src.config import settings
from src.run_registry import upsert_run


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        current = cls(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
        if tz is None:
            return current.replace(tzinfo=None)
        return current.astimezone(tz)


def _seed_run(path, *, run_id: str, status: str, category: str, display_name: str, dept: str | None, created_at: str, decision: str | None = None, decision_at: str | None = None) -> None:
    upsert_run(
        run_id,
        status,
        email_input={
            "subject": f"Sujet {run_id}",
            "author": "client@example.com",
            "category": category,
            "category_display_name": display_name,
            "workflow_dept": dept,
        },
        path=path,
        user_id="alice",
        agent_instance_id="finance-agent",
        created_at=created_at,
        decision=decision,
        decision_at=decision_at,
    )


def test_analytics_summary_aggregates_period_metrics(monkeypatch, tmp_path):
    path = tmp_path / "run_index.json"
    monkeypatch.setattr(analytics, "datetime", FixedDateTime)
    monkeypatch.setattr(analytics, "_path", lambda *_args, **_kwargs: path)
    monkeypatch.setattr(settings, "run_registry_backend", "json")

    _seed_run(
        path,
        run_id="run-1",
        status="completed",
        category="refund",
        display_name="Remboursements",
        dept="Finance",
        created_at="2026-07-10T08:00:00+00:00",
        decision="approved",
        decision_at="2026-07-10T10:00:00+00:00",
    )
    _seed_run(
        path,
        run_id="run-2",
        status="completed",
        category="refund",
        display_name="Remboursements",
        dept="Finance",
        created_at="2026-07-09T09:00:00+00:00",
        decision="rejected",
        decision_at="2026-07-09T09:30:00+00:00",
    )
    _seed_run(
        path,
        run_id="run-3",
        status="pending_approval",
        category="travel",
        display_name="Voyages",
        dept="HR",
        created_at="2026-07-08T14:00:00+00:00",
    )
    _seed_run(
        path,
        run_id="run-4",
        status="completed",
        category="legacy",
        display_name="Ancien",
        dept="Ops",
        created_at="2026-06-01T12:00:00+00:00",
        decision="approved",
        decision_at="2026-06-01T12:20:00+00:00",
    )

    summary = analytics.summarize(
        "week",
        user_id="alice",
        agent_instance_id="finance-agent",
    )

    assert summary["totals"] == {
        "emails_handled": 3,
        "pending": 1,
        "approved": 1,
        "rejected": 1,
        "approval_rate_pct": 50.0,
        "avg_turnaround_seconds": 4500.0,
        "decided_count": 2,
    }
    assert summary["top_categories"][0]["display_name"] == "Remboursements"
    assert summary["top_categories"][0]["count"] == 2
    assert summary["by_workflow"][0]["avg_turnaround_seconds"] == 4500.0
    assert summary["dept_load"][0] == {
        "dept": "Finance",
        "count": 2,
        "pending": 0,
        "approved": 1,
        "rejected": 1,
    }
    assert sum(point["count"] for point in summary["volume_timeline"]) == 3


def test_analytics_summary_filters_department_for_viewers(monkeypatch, tmp_path):
    path = tmp_path / "run_index.json"
    monkeypatch.setattr(analytics, "datetime", FixedDateTime)
    monkeypatch.setattr(analytics, "_path", lambda *_args, **_kwargs: path)
    monkeypatch.setattr(settings, "run_registry_backend", "json")

    _seed_run(
        path,
        run_id="run-finance",
        status="completed",
        category="refund",
        display_name="Remboursements",
        dept="Finance",
        created_at="2026-07-10T08:00:00+00:00",
        decision="approved",
        decision_at="2026-07-10T08:10:00+00:00",
    )
    _seed_run(
        path,
        run_id="run-hr",
        status="completed",
        category="travel",
        display_name="Voyages",
        dept="HR",
        created_at="2026-07-10T09:00:00+00:00",
        decision="rejected",
        decision_at="2026-07-10T09:20:00+00:00",
    )

    summary = analytics.summarize(
        "day",
        user_id="alice",
        agent_instance_id="finance-agent",
        workflow_dept="Finance",
    )

    assert summary["totals"]["emails_handled"] == 1
    assert summary["dept_load"] == [{"dept": "Finance", "count": 1, "pending": 0, "approved": 1, "rejected": 0}]


def test_analytics_endpoint_validates_period_and_scopes_dept(monkeypatch):
    calls = []

    def fake_summarize(period, *, user_id, agent_instance_id, workflow_dept=None):
        calls.append({
            "period": period,
            "user_id": user_id,
            "agent_instance_id": agent_instance_id,
            "workflow_dept": workflow_dept,
        })
        return {"period": period, "totals": {"emails_handled": 0}}

    monkeypatch.setattr(api, "summarize_analytics", fake_summarize)

    with TestClient(app) as client:
        response = client.get(
            "/analytics?period=week",
            headers={
                "X-Agora-User": "alice",
                "X-Agora-Agent-Instance": "finance-agent",
                "X-Agora-Instance-Role": "approver",
                "X-Agora-User-Dept": "Finance",
            },
        )
        assert response.status_code == 200
        assert calls[-1]["workflow_dept"] == "Finance"

        owner = client.get(
            "/analytics?period=month",
            headers={
                "X-Agora-User": "alice",
                "X-Agora-Agent-Instance": "finance-agent",
                "X-Agora-Instance-Role": "owner",
                "X-Agora-User-Dept": "Finance",
            },
        )
        assert owner.status_code == 200
        assert calls[-1]["workflow_dept"] is None

        bad = client.get("/analytics?period=session")
        assert bad.status_code == 422
        assert "day, week, month" in bad.json()["detail"]
