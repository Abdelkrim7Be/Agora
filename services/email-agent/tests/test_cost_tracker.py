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
    assert compute_cost("mistral/mistral-small-latest", 1_000_000, 1_000_000) == 0.75
    assert compute_cost("mistral/mistral-large-latest", 1_000_000, 1_000_000) == 2.0
    assert compute_cost("openai:agora-draft", 1_000_000, 1_000_000) == 2.0
    assert compute_cost("unknown-model", 1_000_000, 1_000_000) == 0.0


def test_compute_cost_discounts_mistral_cached_input_tokens():
    assert compute_cost(
        "mistral/mistral-large-latest",
        1_000_000,
        0,
        cached_input_tokens=640_000,
    ) == 0.212
    assert compute_cost(
        "openai:agora-draft",
        1_000_000,
        0,
        cached_input_tokens=640_000,
    ) == 0.212


def test_usage_callback_discounts_cached_mistral_tokens(monkeypatch, tmp_path):
    path = tmp_path / "costs.jsonl"
    monkeypatch.setattr(settings, "cost_tracking_enabled", True)
    monkeypatch.setattr(settings, "cost_backend", "json")
    monkeypatch.setattr(settings, "costs_path", str(path))

    message = SimpleNamespace(
        usage_metadata={
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "input_token_details": {"cached_tokens": 640_000},
        },
        response_metadata={"model_name": "mistral/mistral-large-latest"},
    )
    generation = SimpleNamespace(message=message)
    response = SimpleNamespace(generations=[[generation]], llm_output={})

    UsageCallback(run_id="run-1", node="draft").on_llm_end(response)

    rows = list_costs(path=path, user_id=None, agent_instance_id=None)
    assert rows[0]["cost_eur"] == 0.212


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


def test_a_locally_served_model_is_priced_not_free(tmp_path):
    """An unpriced model books at zero, which reads as "the work was free" on the
    costs page. Every model the platform actually runs needs a row."""
    from src.cost_tracker import compute_cost

    assert compute_cost("qwen3:4b-4k", 1_000_000, 0) > 0
    assert compute_cost("qwen3:4b-8k", 0, 1_000_000) > 0


def test_the_summary_names_models_it_could_not_price(tmp_path, monkeypatch):
    from src import cost_tracker

    path = tmp_path / "costs.jsonl"
    monkeypatch.setattr(cost_tracker.settings, "cost_backend", "json")
    cost_tracker.record_cost({
        "node": "llm_call", "model": "some-model-nobody-priced",
        "input_tokens": 100, "output_tokens": 10, "cost_eur": 0.0,
    }, path=path)

    summary = cost_tracker.summarize("month", path=path, user_id=None, agent_instance_id=None)

    assert "some-model-nobody-priced" in summary["unpriced_models"]


def test_quarantine_usage_reported_by_the_security_service_is_recorded(tmp_path, monkeypatch):
    """The security service runs a model on every inbound message and keeps no
    ledger of its own; its tokens have to land in the same place as the agent's."""
    from src import cost_tracker, security_client

    path = tmp_path / "costs.jsonl"
    monkeypatch.setattr(cost_tracker.settings, "cost_backend", "json")
    monkeypatch.setattr(cost_tracker.settings, "costs_path", str(path))

    security_client._record_quarantine_usage({
        "model": "qwen3:4b-4k", "input_tokens": 900, "output_tokens": 40,
    })

    entries = cost_tracker.list_costs(path=path, user_id=None, agent_instance_id=None)
    quarantine = [e for e in entries if e["node"] == "quarantine"]
    assert len(quarantine) == 1
    assert quarantine[0]["total_tokens"] == 940
    assert quarantine[0]["cost_eur"] > 0


def test_a_sanitize_verdict_without_usage_records_nothing(tmp_path, monkeypatch):
    from src import cost_tracker, security_client

    path = tmp_path / "costs.jsonl"
    monkeypatch.setattr(cost_tracker.settings, "cost_backend", "json")
    monkeypatch.setattr(cost_tracker.settings, "costs_path", str(path))

    # The common case: heuristics settled it and no model ran.
    security_client._record_quarantine_usage(None)
    security_client._record_quarantine_usage({"model": "x", "input_tokens": 0, "output_tokens": 0})

    assert cost_tracker.list_costs(path=path, user_id=None, agent_instance_id=None) == []
