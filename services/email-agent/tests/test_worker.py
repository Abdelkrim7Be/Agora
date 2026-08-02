from __future__ import annotations

import pytest

from src.automation import RulesConfig
from src.tenant import current_agent_instance_id
import src.worker as worker


async def test_run_worker_once_returns_false_when_queue_empty(monkeypatch):
    monkeypatch.setattr(worker, "claim_job", lambda worker_id: None)

    claimed = await worker.run_worker_once(object())

    assert claimed is False


async def test_run_worker_once_processes_the_claimed_job_in_its_own_instance_context(monkeypatch):
    job = {"id": 7, "instance_id": "agent-b", "message_id": "m1"}
    monkeypatch.setattr(worker, "claim_job", lambda worker_id: job)
    monkeypatch.setattr(worker, "load_rules", lambda: RulesConfig())
    monkeypatch.setattr(worker, "get_provider", lambda: "provider:agent-b")

    seen_instance = {}
    marked_done = []

    async def fake_process(graph, message_id, resource, rules_config):
        seen_instance["id"] = current_agent_instance_id()
        assert message_id == "m1"
        assert resource == "provider:agent-b"
        return (message_id, "completed", "run-1")

    monkeypatch.setattr(worker, "process_message_with_retry", fake_process)
    monkeypatch.setattr(worker, "mark_job_done", lambda job_id: marked_done.append(job_id))

    claimed = await worker.run_worker_once(object())

    assert claimed is True
    assert seen_instance["id"] == "agent-b"
    assert marked_done == [7]
    # The job's instance context does not leak past processing.
    assert current_agent_instance_id() != "agent-b"


async def test_run_worker_once_leaves_job_processing_on_exception(monkeypatch):
    """A crash mid-job must not mark it done — requeue_stale_jobs recovers it later."""
    job = {"id": 9, "instance_id": "agent-c", "message_id": "m2"}
    monkeypatch.setattr(worker, "claim_job", lambda worker_id: job)
    monkeypatch.setattr(worker, "load_rules", lambda: RulesConfig())
    monkeypatch.setattr(worker, "get_provider", lambda: "provider:agent-c")

    async def boom(*args, **kwargs):
        raise RuntimeError("gmail auth blip")

    monkeypatch.setattr(worker, "process_message_with_retry", boom)
    marked_done = []
    monkeypatch.setattr(worker, "mark_job_done", lambda job_id: marked_done.append(job_id))

    claimed = await worker.run_worker_once(object())

    assert claimed is True
    assert marked_done == []
