from __future__ import annotations

from types import SimpleNamespace

from src.config import settings
from src.cost_tracker import UsageCallback, compute_cost, list_costs, record_cost, summarize


def _response(model: str, input_tokens: int, output_tokens: int):
    message = SimpleNamespace(
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens},
        response_metadata={"model_name": model},
    )
    generation = SimpleNamespace(message=message)
    return SimpleNamespace(generations=[[generation]], llm_output={})


def test_usage_callback_records_usage_metadata(monkeypatch, tmp_path):
    path = tmp_path / "costs.jsonl"
    monkeypatch.setattr(settings, "cost_tracking_enabled", True)
    monkeypatch.setattr(settings, "cost_backend", "json")
    monkeypatch.setattr(settings, "costs_path", str(path))

    cb = UsageCallback(
        run_id="run-1",
        node="triage",
        user_id="alice@example.com",
        agent_instance_id="ceo-email-agent",
    )
    cb.on_llm_end(_response("test-model", 1000, 2000))

    rows = list_costs(
        user_id="alice@example.com",
        agent_instance_id="ceo-email-agent",
        path=path,
    )
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["node"] == "triage"
    assert rows[0]["input_tokens"] == 1000
    assert rows[0]["output_tokens"] == 2000
    assert rows[0]["cost_eur"] == 0.0


def test_compute_cost_known_and_unknown_models():
    assert compute_cost("llama-3.3-70b-versatile", 1_000_000, 1_000_000) == 1.33
    assert compute_cost("unknown-model", 1_000_000, 1_000_000) == 0.0


def test_summarize_filters_by_user_and_agent_instance(tmp_path):
    path = tmp_path / "costs.jsonl"
    record_cost({
        "timestamp": "2026-06-24T10:00:00+00:00",
        "user_id": "alice@example.com",
        "agent_instance_id": "ceo-email-agent",
        "run_id": "run-1",
        "node": "triage",
        "model": "model-a",
        "input_tokens": 10,
        "output_tokens": 20,
        "cost_eur": 0.12,
    }, path=path)
    record_cost({
        "timestamp": "2026-06-24T10:01:00+00:00",
        "user_id": "alice@example.com",
        "agent_instance_id": "hr-email-agent",
        "run_id": "run-2",
        "node": "llm_call",
        "model": "model-a",
        "input_tokens": 100,
        "output_tokens": 200,
        "cost_eur": 9.99,
    }, path=path)
    record_cost({
        "timestamp": "2026-06-24T10:02:00+00:00",
        "user_id": "bob@example.com",
        "agent_instance_id": "ceo-email-agent",
        "run_id": "run-3",
        "node": "triage",
        "model": "model-b",
        "input_tokens": 100,
        "output_tokens": 200,
        "cost_eur": 9.99,
    }, path=path)

    summary = summarize(
        "session",
        user_id="alice@example.com",
        agent_instance_id="ceo-email-agent",
        path=path,
    )

    assert summary["totals"] == {
        "calls": 1,
        "input_tokens": 10,
        "output_tokens": 20,
        "total_tokens": 30,
        "cost_eur": 0.12,
    }
    assert summary["by_model"] == {
        "model-a": {
            "calls": 1,
            "input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 30,
            "cost_eur": 0.12,
        }
    }
    assert summary["by_node"]["triage"]["calls"] == 1
