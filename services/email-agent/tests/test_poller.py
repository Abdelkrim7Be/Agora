from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

import src.poller as poller
from src.graph import overall_workflow
from tests.conftest import ai_tool_call


def _raw_message_with_pdf(msg_id: str) -> dict:
    """Message with a PDF attachment part to test gating logic."""
    body_data = base64.urlsafe_b64encode(b"see attached").decode()
    return {
        "id": msg_id,
        "threadId": f"thread-{msg_id}",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Invoice attached"},
            ],
            "parts": [
                {"mimeType": "text/plain", "filename": "", "body": {"data": body_data}},
                {
                    "mimeType": "application/pdf",
                    "filename": "invoice.pdf",
                    "body": {"attachmentId": "att_abc", "size": 50000},
                },
            ],
        },
    }


def _raw_message(msg_id: str, subject: str, body: str) -> dict:
    data = base64.urlsafe_b64encode(body.encode()).decode()
    return {
        "id": msg_id,
        "threadId": f"thread-{msg_id}",
        "payload": {
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": data},
        },
    }


@pytest.fixture
def mocked_gmail(monkeypatch):
    """Patch the Gmail calls poll_once uses; record mark_as_read invocations."""
    marked: list[str] = []
    messages: dict[str, dict] = {}

    def set_unread(refs_and_messages: list[dict]):
        messages.clear()
        for m in refs_and_messages:
            messages[m["id"]] = m
        monkeypatch.setattr(
            poller, "fetch_unread", lambda max_results, resource=None: [{"id": k} for k in messages]
        )

    monkeypatch.setattr(poller, "get_message", lambda msg_id, resource=None: messages[msg_id])
    # Single-message thread — keeps poll_once behavior assertions focused.
    monkeypatch.setattr(
        poller,
        "fetch_thread",
        lambda thread_id, resource=None: [m for m in messages.values() if m["threadId"] == thread_id],
    )
    monkeypatch.setattr(poller, "mark_as_read", lambda msg_id, resource=None: marked.append(msg_id))
    return set_unread, marked


def _graph():
    return overall_workflow.compile(checkpointer=MemorySaver(), store=InMemoryStore())


async def test_poll_once_marks_completed_runs_read(mocked_gmail, fake_llms):
    set_unread, marked = mocked_gmail
    set_unread([
        _raw_message("m1", "FYI newsletter", "deals deals deals"),
        _raw_message("m2", "Another digest", "more deals"),
    ])
    fake_llms(classification="ignore")

    outcomes = await poller.poll_once(_graph(), resource=object())

    assert len(outcomes) == 2
    assert all(status == "completed" for _, status, _ in outcomes)
    assert marked == ["m1", "m2"]


async def test_poll_once_leaves_paused_runs_unread(mocked_gmail, fake_llms):
    set_unread, marked = mocked_gmail
    set_unread([_raw_message("m3", "Quick question", "can you help?")])
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", {"to": "a@b.com", "subject": "Re", "content": "Hi"}, "c1"),
        ],
    )

    outcomes = await poller.poll_once(_graph(), resource=object())

    assert outcomes == [("m3", "pending_approval", outcomes[0][2])]
    assert marked == []  # paused run must stay unread


async def test_poll_once_empty_inbox(mocked_gmail, fake_llms):
    set_unread, marked = mocked_gmail
    set_unread([])
    fake_llms(classification="ignore")

    outcomes = await poller.poll_once(_graph(), resource=object())

    assert outcomes == []
    assert marked == []


async def test_pdf_not_downloaded_when_extraction_disabled(mocked_gmail, fake_llms, monkeypatch):
    """download_attachment must never be called when AGENT_EXTRACT_ATTACHMENTS is false."""
    set_unread, _ = mocked_gmail
    set_unread([_raw_message_with_pdf("m_pdf")])
    fake_llms(classification="ignore")

    download_mock = MagicMock()
    monkeypatch.setattr(poller, "download_attachment", download_mock)
    monkeypatch.setattr(poller.settings, "extract_attachments", False)

    await poller.poll_once(_graph(), resource=object())

    download_mock.assert_not_called()


class _RecordingGraph:
    """Captures the state passed to the graph so we can assert on injected context."""

    def __init__(self):
        self.inputs: list[dict] = []

    async def ainvoke(self, state, cfg):
        self.inputs.append(state)
        return {}  # no __interrupt__ → run completes


async def test_pdf_extracted_and_injected_when_enabled(mocked_gmail, monkeypatch):
    """Flag ON: poll_once downloads the PDF, extracts text, and folds it into email_thread."""
    set_unread, _ = mocked_gmail
    set_unread([_raw_message_with_pdf("m_pdf")])

    monkeypatch.setattr(poller.settings, "extract_attachments", True)
    monkeypatch.setattr(poller, "download_attachment", lambda *a, **k: b"raw-pdf-bytes")
    monkeypatch.setattr(poller, "extract_pdf_text", lambda data, max_chars: "INVOICE TOTAL 500")

    graph = _RecordingGraph()
    await poller.poll_once(graph, resource=object())

    assert len(graph.inputs) == 1
    thread = graph.inputs[0]["email_input"]["email_thread"]
    assert "Attachment contents:" in thread
    assert "INVOICE TOTAL 500" in thread
    assert "invoice.pdf" in thread  # filename header in the injected block
