from __future__ import annotations

import json

import src.api as api
from src.config import settings


class _FakeRequest:
    """Disconnects after `alive_ticks` calls to is_disconnected() — lets a test
    bound an otherwise-infinite stream deterministically instead of racing a timer."""

    def __init__(self, alive_ticks: int):
        self._remaining = alive_ticks

    async def is_disconnected(self) -> bool:
        if self._remaining <= 0:
            return True
        self._remaining -= 1
        return False


def _run(run_id: str, updated_at: str, **overrides) -> dict:
    base = {
        "run_id": run_id,
        "status": "pending_approval",
        "classification": "respond",
        "pending_action": None,
        "updated_at": updated_at,
        "workflow_dept": None,
    }
    base.update(overrides)
    return base


async def _collect(monkeypatch, runs_by_tick: list[list[dict]], alive_ticks: int, user_dept: str | None = None):
    """alive_ticks must be len(runs_by_tick) to let every tick (baseline + one
    comparison per remaining entry) run before the fake request disconnects."""
    calls = iter(runs_by_tick)
    monkeypatch.setattr(api, "list_runs", lambda **kwargs: next(calls, runs_by_tick[-1]))
    monkeypatch.setattr(settings, "events_poll_interval_seconds", 0)

    events = []
    request = _FakeRequest(alive_ticks)
    async for chunk in api._events_generator(request, "instance-a", user_dept):
        events.append(chunk)
    return events


def _parse(chunk: str) -> tuple[str, dict]:
    event_line, data_line = chunk.strip("\n").split("\n", 1)
    event = event_line.split(":", 1)[1].strip()
    data = json.loads(data_line.split("data:", 1)[1].strip())
    return event, data


async def test_first_tick_only_baselines_no_events_emitted(monkeypatch):
    """A fresh connection must not replay existing runs as 'new' events."""
    runs = [_run("r1", "2026-01-01T00:00:00+00:00")]
    # 3 ticks total: baseline, then 2 no-change comparisons -> 2 heartbeats.
    events = await _collect(monkeypatch, [runs, runs, runs], alive_ticks=3)

    kinds = [e for e, _ in (_parse(c) for c in events)]
    assert "run_updated" not in kinds
    assert kinds == ["heartbeat", "heartbeat"]


async def test_changed_run_emits_run_updated(monkeypatch):
    tick1 = [_run("r1", "2026-01-01T00:00:00+00:00")]
    tick2 = [_run("r1", "2026-01-01T00:00:05+00:00", status="completed")]
    # baseline=tick1, comparison#1 (tick2 vs tick1) -> run_updated, comparison#2 (tick2 vs tick2) -> heartbeat.
    events = await _collect(monkeypatch, [tick1, tick2, tick2], alive_ticks=3)

    kinds_and_data = [_parse(c) for c in events]
    assert kinds_and_data[0][0] == "run_updated"
    assert kinds_and_data[0][1]["run_id"] == "r1"
    assert kinds_and_data[0][1]["status"] == "completed"
    assert kinds_and_data[1][0] == "heartbeat"


async def test_new_run_emits_run_updated(monkeypatch):
    tick1: list[dict] = []
    tick2 = [_run("r_new", "2026-01-01T00:00:05+00:00")]
    events = await _collect(monkeypatch, [tick1, tick2, tick2], alive_ticks=3)

    kinds_and_data = [_parse(c) for c in events]
    assert kinds_and_data[0][0] == "run_updated"
    assert kinds_and_data[0][1]["run_id"] == "r_new"


async def test_dept_scoped_run_is_filtered_out(monkeypatch):
    tick1 = [_run("r1", "2026-01-01T00:00:00+00:00", workflow_dept="finance")]
    tick2 = [_run("r1", "2026-01-01T00:00:05+00:00", status="completed", workflow_dept="finance")]
    events = await _collect(monkeypatch, [tick1, tick2, tick2], alive_ticks=3, user_dept="hr")

    kinds = [e for e, _ in (_parse(c) for c in events)]
    assert "run_updated" not in kinds
    assert kinds == ["heartbeat", "heartbeat"]


async def test_stops_when_client_disconnects(monkeypatch):
    runs = [_run("r1", "2026-01-01T00:00:00+00:00")]
    events = await _collect(monkeypatch, [runs, runs, runs], alive_ticks=0)

    assert events == []


async def test_events_route_streams_sse_content_type(monkeypatch):
    """Route wiring smoke test: correct content-type, well-formed first frame."""
    runs = [_run("r1", "2026-01-01T00:00:00+00:00")]
    monkeypatch.setattr(api, "list_runs", lambda **kwargs: runs)
    monkeypatch.setattr(api, "_request_user_dept", lambda request: None)
    monkeypatch.setattr(settings, "events_poll_interval_seconds", 0)

    request = _FakeRequest(alive_ticks=2)
    response = await api.events_stream(request)
    assert response.media_type == "text/event-stream"

    first_chunk = await response.body_iterator.__anext__()
    event, _ = _parse(first_chunk)
    assert event == "heartbeat"
    await response.body_iterator.aclose()
