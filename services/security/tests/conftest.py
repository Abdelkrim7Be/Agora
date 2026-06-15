from __future__ import annotations

import pytest

from src.models import QuarantineVerdict


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
