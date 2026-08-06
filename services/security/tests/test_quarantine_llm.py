from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.models import QuarantineVerdict, TrustClassificationVerdict
from src.quarantine_llm import (
    ToollessQuarantineClient,
    build_quarantine_classifier,
    build_trust_classifier,
)

client = TestClient(app)


class FakeModel:
    model_name = "fake-quarantine"

    def __init__(self):
        self.structured_schema = None

    def invoke(self, _messages, config=None):
        if self.structured_schema is QuarantineVerdict:
            return QuarantineVerdict(
                injection=False,
                spam=False,
                reasons=[],
                spans=[],
            )
        if self.structured_schema is TrustClassificationVerdict:
            return TrustClassificationVerdict(trust="UNTRUSTED", reasons=[])
        return "free text"

    def bind_tools(self, *_args, **_kwargs):
        return "bound"

    def with_structured_output(self, schema):
        child = FakeModel()
        child.structured_schema = schema
        return child


class MalformedLLM:
    def invoke(self, _messages):
        return {"not": "a QuarantineVerdict"}


def test_quarantine_client_cannot_bind_tools():
    client_model = ToollessQuarantineClient(FakeModel())

    with pytest.raises(RuntimeError, match="cannot bind tools"):
        client_model.bind_tools([])


def test_quarantine_classifier_uses_structured_schema(monkeypatch):
    monkeypatch.setattr("src.quarantine_llm.init_chat_model", lambda *_args, **_kwargs: FakeModel())

    classifier = build_quarantine_classifier()
    verdict = classifier.invoke([])

    assert verdict.spans == []
    with pytest.raises(RuntimeError, match="cannot bind tools"):
        classifier.bind_tools([])


def test_trust_classifier_uses_structured_schema(monkeypatch):
    monkeypatch.setattr(
        "src.quarantine_llm.init_chat_model", lambda *_args, **_kwargs: FakeModel()
    )

    classifier = build_trust_classifier()
    verdict = classifier.invoke([])

    assert verdict.trust == "UNTRUSTED"
    with pytest.raises(RuntimeError, match="cannot bind tools"):
        classifier.bind_tools([])


def test_malformed_quarantine_output_degrades(fake_quarantine, monkeypatch):
    import src.config as cfg
    import src.sanitize as sanitize_module

    monkeypatch.setattr(cfg.settings, "sanitize_always_llm", True)
    monkeypatch.setattr(sanitize_module, "quarantine_llm", MalformedLLM())

    r = client.post(
        "/sanitize",
        json={
            "sender": "alice@example.com",
            "subject": "Question",
            "content": "Can you help me with the report?",
        },
    )

    assert r.status_code == 200
    body = r.json()
    assert body["classification"] == "benign"
    assert body["classifier_unavailable"] is True
    assert "classifier_unavailable" in body["reasons"]
