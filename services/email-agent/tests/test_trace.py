from __future__ import annotations

from fastapi.testclient import TestClient

import src.graph as graph
from src.api import app


def test_traced_node_records_usage_without_changing_output(monkeypatch):
    recorded = []
    snapshots = [
        {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_eur": 0.0},
        {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18, "cost_eur": 0.12},
    ]

    monkeypatch.setattr(graph, "totals_for_run_node", lambda run_id, node: snapshots.pop(0))
    monkeypatch.setattr(graph, "record_trace", lambda entry: recorded.append(entry))

    wrapped = graph._traced_node(
        "triage_router",
        lambda state, store, config=None: {"classification_decision": "notify", **state},
    )
    result = wrapped({"email_input": {"subject": "Hello"}}, object(), {"configurable": {"thread_id": "run-1"}})

    assert result["classification_decision"] == "notify"
    assert recorded == [
        {
            "run_id": "run-1",
            "node": "triage_router",
            "status": "ok",
            "latency_ms": recorded[0]["latency_ms"],
            "started_at": recorded[0]["started_at"],
            "finished_at": recorded[0]["finished_at"],
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "cost_eur": 0.12,
            "error": "",
        }
    ]


def test_traced_node_reraises_and_marks_error(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        graph,
        "totals_for_run_node",
        lambda run_id, node: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_eur": 0.0},
    )
    monkeypatch.setattr(graph, "record_trace", lambda entry: recorded.append(entry))

    wrapped = graph._traced_node(
        "environment",
        lambda state, store, config=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    try:
        wrapped({"email_input": {}}, object(), {"configurable": {"thread_id": "run-2"}})
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:  # pragma: no cover - defensive
        raise AssertionError("RuntimeError was expected")

    assert recorded[0]["status"] == "error"
    assert recorded[0]["error"] == "boom"


def test_run_detail_includes_trace(monkeypatch):
    import src.api as api

    class State:
        values = {"email_input": {"subject": "Trace me", "author": "Alice"}, "messages": []}

    class Graph:
        async def aget_state(self, config):
            return State()

    monkeypatch.setattr(api, "get_run_record", lambda run_id, user_id=None, agent_instance_id=None: {"run_id": run_id, "status": "completed"})
    monkeypatch.setattr(api, "list_traces", lambda run_id=None, user_id=None, agent_instance_id=None, limit=500: [{"node": "triage_router", "status": "ok", "latency_ms": 12}])

    with TestClient(app) as client:
        client.app.state.graph = Graph()
        response = client.get("/run/run-1/detail")

    assert response.status_code == 200
    assert response.json()["trace"] == [{"node": "triage_router", "status": "ok", "latency_ms": 12}]
