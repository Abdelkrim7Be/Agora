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
    "source_trust": "UNTRUSTED",
    "fields": {
        "sender": {"value": "alice@example.com", "trust": "UNTRUSTED"},
        "subject": {"value": "Hello", "trust": "UNTRUSTED"},
        "body": {"value": "Hello, just checking in!", "trust": "UNTRUSTED"},
    },
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

AUTHORIZE_ALLOW = {"decision": "allow", "reason": "allowed by policy"}


class _FakeSyncClient:
    def __init__(self, *, response=None, raises=None, calls=None, **_kw):
        self._response = response
        self._raises = raises
        self._calls = calls if calls is not None else []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def post(self, url: str, json=None):
        if self._raises:
            raise self._raises
        self._calls.append({"url": url, "json": json})
        return self._response


def test_authorize_action_200_returns_verdict_and_sends_run_id(monkeypatch):
    from src.security_client import authorize_action

    calls = []
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: _FakeSyncClient(
            response=_FakeResponse(AUTHORIZE_ALLOW), calls=calls, **kw
        ),
    )

    result = authorize_action(
        "write_email",
        {"to": "alice@example.com", "subject": "Hi", "content": "Hello"},
        "run-123",
        "call-1",
    )

    assert result == AUTHORIZE_ALLOW
    assert calls[0]["url"].endswith("/authorize")
    assert calls[0]["json"]["action"] == "write_email"
    assert calls[0]["json"]["context"] == {
        "run_id": "run-123",
        "action_id": "call-1",
        "user_id": "default",
        "agent_instance_id": "default-email-agent",
    }


def test_authorize_action_failure_denies_closed(monkeypatch):
    from src.security_client import authorize_action

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: _FakeSyncClient(raises=httpx.ConnectError("refused"), **kw),
    )

    result = authorize_action("write_email", {"to": "alice@example.com"}, "run-123")

    assert result["decision"] == "deny"
    assert result["reason"] == "security_service_unreachable"


def test_authorize_action_non_2xx_denies_closed(monkeypatch):
    from src.security_client import authorize_action

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: _FakeSyncClient(response=_FakeResponse({}, raise_on_status=True), **kw),
    )

    result = authorize_action("write_email", {"to": "alice@example.com"}, "run-123")

    assert result["decision"] == "deny"
    assert result["reason"] == "security_service_unreachable"


def test_sanitize_memory_write_200_returns_verdict_and_sends_namespace(monkeypatch):
    from src.security_client import sanitize_memory_write

    calls = []
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: _FakeSyncClient(response=_FakeResponse(BENIGN_VERDICT), calls=calls, **kw),
    )

    result = sanitize_memory_write("email_agent/default/response_preferences", "Prefers short replies.")

    assert result == BENIGN_VERDICT
    assert calls[0]["url"].endswith("/sanitize")
    assert calls[0]["json"]["subject"] == "memory:email_agent/default/response_preferences"
    assert calls[0]["json"]["content"] == "Prefers short replies."


def test_sanitize_memory_write_failure_blocks_closed(monkeypatch):
    from src.security_client import sanitize_memory_write

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: _FakeSyncClient(raises=httpx.ConnectError("refused"), **kw),
    )

    result = sanitize_memory_write("email_agent/default/response_preferences", "some text")

    assert result["injection_detected"] is True
    assert result["classification"] == "malicious"
    assert result["reasons"] == ["security_service_unreachable"]


def test_sanitize_memory_write_non_2xx_blocks_closed(monkeypatch):
    from src.security_client import sanitize_memory_write

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: _FakeSyncClient(response=_FakeResponse({}, raise_on_status=True), **kw),
    )

    result = sanitize_memory_write("email_agent/default/response_preferences", "some text")

    assert result["injection_detected"] is True
    assert result["classification"] == "malicious"
