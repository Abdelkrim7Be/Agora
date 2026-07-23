from __future__ import annotations

from conftest import ai_tool_call
from fastapi.testclient import TestClient

import src.graph as graph_module
from src.ai_assist import _ThreadSummary, _ToneRewrite, adjust_tone, summarize_thread
from src.api import app

DRAFT = {"to": "alice@example.com", "subject": "Re: question", "content": "Here you go."}


class _FakeSummaryLLM:
    def __init__(self, text="Résumé court."):
        self._text = text
        self.calls = 0

    def with_structured_output(self, schema):
        assert schema is _ThreadSummary
        return self

    def invoke(self, messages, config=None):
        self.calls += 1
        return _ThreadSummary(summary=self._text)


class _FakeToneLLM:
    def __init__(self, text="Version reformulée."):
        self._text = text
        self.calls = 0

    def with_structured_output(self, schema):
        assert schema is _ToneRewrite
        return self

    def invoke(self, messages, config=None):
        self.calls += 1
        return _ToneRewrite(content=self._text)


def test_summarize_thread_empty_input_skips_llm():
    llm = _FakeSummaryLLM()
    assert summarize_thread("", llm) == ""
    assert summarize_thread("   ", llm) == ""
    assert llm.calls == 0


def test_summarize_thread_returns_llm_output():
    llm = _FakeSummaryLLM("Client demande un remboursement.")
    assert summarize_thread("some thread text", llm) == "Client demande un remboursement."
    assert llm.calls == 1


def test_adjust_tone_unknown_tone_returns_input_unchanged():
    llm = _FakeToneLLM()
    assert adjust_tone("Bonjour, voici ma réponse.", "sarcastic", llm) == "Bonjour, voici ma réponse."
    assert llm.calls == 0


def test_adjust_tone_empty_input_returns_empty():
    llm = _FakeToneLLM()
    assert adjust_tone("", "formel", llm) == ""
    assert llm.calls == 0


def test_adjust_tone_rewrites_via_llm():
    llm = _FakeToneLLM("Bonjour, je vous confirme la réception de votre demande.")
    result = adjust_tone("salut, c bon j'ai recu ta demande", "formel", llm)
    assert result == "Bonjour, je vous confirme la réception de votre demande."
    assert llm.calls == 1


def test_summarize_endpoint_returns_thread_summary(fake_llms, respond_email, monkeypatch):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    monkeypatch.setattr(graph_module, "llm", _FakeSummaryLLM("Résumé du fil."))
    with TestClient(app) as client:
        run = client.post("/run", json=respond_email).json()
        assert run["status"] == "pending_approval"

        result = client.post(f"/run/{run['run_id']}/summarize").json()
        assert result["summary"] == "Résumé du fil."


def test_summarize_endpoint_unknown_run_404():
    with TestClient(app) as client:
        resp = client.post("/run/does-not-exist/summarize")
    assert resp.status_code == 404


def test_tone_endpoint_rewrites_pending_draft(fake_llms, respond_email, monkeypatch):
    fake_llms(
        classification="respond",
        tool_sequence=[
            ai_tool_call("write_email", DRAFT, "c1"),
            ai_tool_call("Done", {"done": True}, "c2"),
        ],
    )
    monkeypatch.setattr(graph_module, "llm", _FakeToneLLM("Bonjour, voici une version plus formelle."))
    with TestClient(app) as client:
        run = client.post("/run", json=respond_email).json()
        run_id = run["run_id"]

        result = client.post(f"/run/{run_id}/tone", json={"tone": "formel"}).json()
        assert result["field"] == "content"
        assert result["content"] == "Bonjour, voici une version plus formelle."

        # Suggestion is not applied on its own — the pending draft is unchanged.
        detail = client.get(f"/run/{run_id}").json()
        assert detail["status"] == "pending_approval"


def test_tone_endpoint_rejects_unknown_tone(fake_llms, respond_email):
    fake_llms(
        classification="respond",
        tool_sequence=[ai_tool_call("write_email", DRAFT, "c1")],
    )
    with TestClient(app) as client:
        run = client.post("/run", json=respond_email).json()
        resp = client.post(f"/run/{run['run_id']}/tone", json={"tone": "sarcastic"})
    assert resp.status_code == 400


def test_tone_endpoint_no_pending_draft_404():
    with TestClient(app) as client:
        resp = client.post("/run/does-not-exist/tone", json={"tone": "formel"})
    assert resp.status_code == 404
