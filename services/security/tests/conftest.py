from __future__ import annotations

import pytest

from src.models import QuarantineVerdict, TrustClassificationVerdict


@pytest.fixture(autouse=True)
def _offline_trust_classifier(monkeypatch):
    import src.classify as classification

    monkeypatch.setattr(classification, "trust_classifier", _FakeTrustLLM())


@pytest.fixture(autouse=True)
def _reset_ratelimit():
    import src.ratelimit as r
    r.reset()
    yield
    r.reset()


class _FakeQuarantineLLM:
    def __init__(self, verdict: QuarantineVerdict):
        self._v = verdict

    def invoke(self, _messages):
        return self._v


class _FakeTrustLLM:
    def __init__(self, trust: str = "TRUSTED", reasons: list[str] | None = None):
        self._verdict = TrustClassificationVerdict(
            trust=trust, reasons=reasons or []
        )

    def invoke(self, _messages):
        return self._verdict


class _BoomLLM:
    def invoke(self, _messages):
        raise RuntimeError("groq down")


@pytest.fixture
def fake_quarantine(monkeypatch):
    """Patch sanitize.quarantine_llm for offline deterministic tests."""
    def _install(
        injection: bool = False,
        spam: bool = False,
        reasons: list[str] | None = None,
        sanitized: str = "clean text",
        raises: bool = False,
    ):
        import src.sanitize as s

        if raises:
            monkeypatch.setattr(s, "quarantine_llm", _BoomLLM())
        else:
            monkeypatch.setattr(
                s,
                "quarantine_llm",
                _FakeQuarantineLLM(
                    QuarantineVerdict(
                        injection=injection,
                        spam=spam,
                        reasons=reasons or [],
                        sanitized=sanitized,
                    )
                ),
            )

    return _install


@pytest.fixture
def fake_output_quarantine(monkeypatch):
    """Patch output_audit.quarantine_llm for offline deterministic tests."""
    def _install(
        injection: bool = False,
        spam: bool = False,
        reasons: list[str] | None = None,
        sanitized: str = "clean text",
        raises: bool = False,
    ):
        import src.output_audit as oa

        if raises:
            monkeypatch.setattr(oa, "quarantine_llm", _BoomLLM())
        else:
            monkeypatch.setattr(
                oa,
                "quarantine_llm",
                _FakeQuarantineLLM(
                    QuarantineVerdict(
                        injection=injection,
                        spam=spam,
                        reasons=reasons or [],
                        sanitized=sanitized,
                    )
                ),
            )

    return _install
