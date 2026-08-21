"""End-to-end regression: a reviewer-staged attachment actually reaches the send.

Slice B's upload endpoint and graph wiring are only meaningful for a tool that
pauses for human approval (write_email) — create_draft never enters this path
since it has no HITL gate. This test exercises the real path: stage a file via
src.run_attachments, pause a write_email draft, approve it carrying the staged
attachment id under the reviewer-only "_attachments" key, and assert the bytes
reach the provider's send_message call.
"""

from __future__ import annotations

import uuid

import pytest
from conftest import ai_tool_call
from langgraph.types import Command

from src.graph import email_assistant
from src.run_attachments import save_attachment
from tests.conftest import patch_provider

DRAFT = {"to": "alice@example.com", "subject": "Re: question", "content": "Here you go."}


@pytest.fixture
def media_tmp(monkeypatch, tmp_path):
    import src.media as media

    monkeypatch.setattr(media.settings, "media_dir", str(tmp_path))
    return tmp_path


def _cfg(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def test_reviewer_uploaded_attachment_reaches_send(monkeypatch, fake_llms, respond_email, media_tmp):
    from src.capabilities import email_tools

    run_id = str(uuid.uuid4())
    entry = save_attachment(run_id, "invoice.pdf", "application/pdf", b"file-bytes")

    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    monkeypatch.setattr(email_tools.settings, "dry_run", False)
    calls: list[dict] = []
    patch_provider(
        monkeypatch,
        email_tools,
        send_message=lambda **kwargs: calls.append(kwargs) or {"id": "sent-1"},
    )

    cfg = _cfg(run_id)
    paused = email_assistant.invoke({"email_input": respond_email}, cfg)
    assert paused["__interrupt__"][0].value[0]["action_request"]["action"] == "write_email"

    done = email_assistant.invoke(
        Command(resume={"type": "approve", "args": {**DRAFT, "_attachments": [entry["attachment_id"]]}}),
        cfg,
    )

    assert "__interrupt__" not in done
    assert calls == [{
        "to": "alice@example.com",
        "subject": "Re: question",
        "body": "Here you go.",
        "attachments": [
            {"filename": "invoice.pdf", "mime_type": "application/pdf", "data": b"file-bytes"}
        ],
    }]
    # The reviewer-only key must never survive into the persisted tool-call args
    # (it would otherwise look like something the model wrote).
    ai_messages = [m for m in done["messages"] if getattr(m, "tool_calls", None)]
    for message in ai_messages:
        for call in message.tool_calls:
            assert "_attachments" not in call.get("args", {})
