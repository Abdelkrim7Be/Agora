from __future__ import annotations

import httpx
import pytest

from src.security_client import sanitize_email

BENIGN_VERDICT = {
    "classification": "benign",
    "injection_detected": False,
    "spam": False,
    "reasons": [],
    "cleaned_text": "Hello, just checking in!",
    "classifier_unavailable": False,
}


class _FakeResponse:
    def __init__(self, data: dict, raise_on_status: bool = False):
        self._data = data
        self._raise = raise_on_status

    def raise_for_status(self):
        if self._raise:
            raise RuntimeError("upstream error")

    def json(self) -> dict:
        return self._data


class _FakeAsyncClient:
    def __init__(self, *, response=None, raises=None, **_kw):
        self._response = response
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def post(self, url: str, json=None):
        if self._raises:
            raise self._raises
        return self._response


async def test_200_returns_verdict_unchanged(monkeypatch):
    """A successful /sanitize response is returned as-is."""
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: _FakeAsyncClient(response=_FakeResponse(BENIGN_VERDICT), **kw),
    )
    result = await sanitize_email("alice@example.com", "Hello", "Hello, just checking in!")
    assert result == BENIGN_VERDICT


async def test_connect_error_returns_cautious_verdict(monkeypatch):
    """A connection error triggers the fail-safe: cautious verdict, original content preserved."""
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: _FakeAsyncClient(raises=httpx.ConnectError("connection refused"), **kw),
    )
    result = await sanitize_email("alice@example.com", "Hello", "some body text")
    assert result["classifier_unavailable"] is True
    assert result["cleaned_text"] == "some body text"
    assert "security_service_unreachable" in result["reasons"]
    assert result["classification"] == "suspicious"


async def test_non_2xx_returns_cautious_verdict(monkeypatch):
    """A non-2xx response (raise_for_status) also triggers the fail-safe."""
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: _FakeAsyncClient(response=_FakeResponse({}, raise_on_status=True), **kw),
    )
    result = await sanitize_email("alice@example.com", "Hello", "some body text")
    assert result["classifier_unavailable"] is True
    assert result["cleaned_text"] == "some body text"
    assert "security_service_unreachable" in result["reasons"]


async def test_timeout_returns_cautious_verdict(monkeypatch):
    """A read timeout is treated the same as any other exception: fail-safe."""
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: _FakeAsyncClient(raises=httpx.ReadTimeout("timeout"), **kw),
    )
    result = await sanitize_email("sender@example.com", "Re: meeting", "Hi")
    assert result["classifier_unavailable"] is True
    assert result["cleaned_text"] == "Hi"
