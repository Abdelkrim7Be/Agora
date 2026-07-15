from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.api import app
from src.config import settings


def test_run_stream_emits_draft_and_result(fake_llms, respond_email, monkeypatch):
    from conftest import ai_tool_call

    monkeypatch.setattr(settings, "llm_streaming_enabled", True)
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("write_email", {"to": "alice@example.com", "subject": "Re: question", "content": "Bonjour Alice,\n\nVoici un premier brouillon.\n\nBien à vous."}, "c1")],
    )

    with TestClient(app) as client:
        with client.stream("POST", "/run/stream", json=respond_email) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: draft" in body
    assert "premier brouillon" in body
    assert "event: result" in body
    assert '"status": "pending_approval"' in body


def test_respond_stream_emits_redraft_progress(fake_llms, respond_email, monkeypatch):
    from conftest import ai_tool_call

    monkeypatch.setattr(settings, "llm_streaming_enabled", True)
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", {"to": "alice@example.com", "subject": "Re: question", "content": "Draft one."}, "c1"),
            ai_tool_call("write_email", {"to": "alice@example.com", "subject": "Re: question", "content": "Draft two, shorter and clearer."}, "c2"),
        ],
    )

    with TestClient(app) as client:
        first = client.post("/run", json=respond_email).json()
        with client.stream("POST", f"/run/{first['run_id']}/respond/stream", json={"feedback": "make it shorter"}) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: status" in body
    assert "event: draft" in body
    assert "Draft two, shorter and clearer." in body
    assert "event: end" in body


def test_streaming_disabled_returns_terminal_result_only(fake_llms, respond_email, monkeypatch):
    from conftest import ai_tool_call

    monkeypatch.setattr(settings, "llm_streaming_enabled", False)
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("write_email", {"to": "alice@example.com", "subject": "Re: question", "content": "No progressive chunks."}, "c1")],
    )

    with TestClient(app) as client:
        with client.stream("POST", "/run/stream", json=respond_email) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: draft" not in body
    assert "event: result" in body
    payload = json.loads(body.split("event: result\ndata: ", 1)[1].split("\n\n", 1)[0])
    assert payload["status"] == "pending_approval"
