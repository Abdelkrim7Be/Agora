from __future__ import annotations

import pytest

import src.gmail_budget as budget
from src.gmail_budget import budget_status, calls_last_hour, record_gmail_call, reset_budget


@pytest.fixture(autouse=True)
def _clean():
    reset_budget()
    yield
    reset_budget()


def test_record_and_count(monkeypatch):
    record_gmail_call(agent_instance_id="box-a")
    record_gmail_call(3, agent_instance_id="box-a")
    assert calls_last_hour("box-a") == 4


def test_counts_are_per_instance():
    record_gmail_call(2, agent_instance_id="box-a")
    record_gmail_call(5, agent_instance_id="box-b")
    assert calls_last_hour("box-a") == 2
    assert calls_last_hour("box-b") == 5


def test_old_calls_fall_out_of_window(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(budget.time, "time", lambda: clock["now"])
    record_gmail_call(agent_instance_id="box-a")
    assert calls_last_hour("box-a") == 1
    clock["now"] = 4000.0  # > 1h later
    assert calls_last_hour("box-a") == 0


def test_budget_status_percentage(monkeypatch):
    monkeypatch.setattr(budget.settings, "gmail_hourly_call_budget", 200)
    record_gmail_call(50, agent_instance_id="box-a")
    status = budget_status("box-a")
    assert status == {"calls_last_hour": 50, "budget": 200, "pct": 25}


def test_alert_fires_once_past_eighty_percent(monkeypatch):
    monkeypatch.setattr(budget.settings, "gmail_hourly_call_budget", 10)
    alerts = {"count": 0}
    real_inc = budget.inc_counter

    def counting_inc(name, amount=1.0, **labels):
        if name == "gmail_budget_alerts_total":
            alerts["count"] += 1
        real_inc(name, amount, **labels)

    monkeypatch.setattr(budget, "inc_counter", counting_inc)
    record_gmail_call(8, agent_instance_id="box-a")   # hits 80% → alert
    record_gmail_call(1, agent_instance_id="box-a")   # still >=80%, no second alert
    assert alerts["count"] == 1
