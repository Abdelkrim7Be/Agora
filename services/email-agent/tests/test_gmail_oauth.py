from __future__ import annotations

import json
import urllib.parse
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.config import settings
from src.gmail_oauth import build_state, validate_state


class _FakeCredentials:
    def to_json(self):
        return '{"token": "oauth-token"}'


class _FakeFlow:
    credentials = _FakeCredentials()
    last = None

    def __init__(self):
        self.fetch_code = None

    @classmethod
    def from_client_secrets_file(cls, client_secrets_file, scopes, redirect_uri):
        flow = cls()
        flow.client_secrets_file = client_secrets_file
        flow.scopes = scopes
        flow.redirect_uri = redirect_uri
        cls.last = flow
        return flow

    def authorization_url(self, **kwargs):
        self.authorization_kwargs = kwargs
        return (
            "https://accounts.google.com/o/oauth2/auth?"
            f"state={kwargs['state']}&access_type={kwargs['access_type']}&prompt={kwargs['prompt']}"
        ), "unused"

    def fetch_token(self, code):
        self.fetch_code = code


def test_gmail_connect_start_builds_signed_offline_consent_url(monkeypatch):
    import src.gmail_oauth as oauth

    monkeypatch.setattr(settings, "gmail_oauth_state_secret", "unit-state-secret")
    monkeypatch.setattr(settings, "gmail_oauth_redirect_uri", "https://gateway.example/api/agent/connect/gmail/callback")
    monkeypatch.setattr(oauth, "Flow", _FakeFlow)

    with TestClient(app) as client:
        response = client.get(
            "/agent-instances/ceo-email-agent/connect/gmail/start?mailbox_identity=ceo@example.com",
            headers={"X-Agora-User": "owner@example.com", "X-Agora-Agent-Instance": "ceo-email-agent"},
        )

    assert response.status_code == 200
    body = response.json()
    parsed = urlparse(body["authorization_url"])
    params = parse_qs(parsed.query)
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert body["agent_instance_id"] == "ceo-email-agent"
    assert "https://mail.google.com/" in body["scopes"]
    assert _FakeFlow.last.redirect_uri == "https://gateway.example/api/agent/connect/gmail/callback"


def test_gmail_connect_callback_rejects_bad_state(monkeypatch):
    monkeypatch.setattr(settings, "gmail_oauth_state_secret", "unit-state-secret")

    with TestClient(app) as client:
        response = client.get("/connect/gmail/callback?code=abc&state=bad-state")

    assert response.status_code == 400
    assert "Invalid OAuth state" in response.json()["detail"]


def test_gmail_connect_callback_exchanges_valid_state(monkeypatch):
    import src.api as api

    monkeypatch.setattr(settings, "gmail_oauth_state_secret", "unit-state-secret")
    state = build_state("owner@example.com", "ceo-email-agent", mailbox_identity="ceo@example.com")
    captured = {}

    def fake_exchange(code, payload):
        captured["code"] = code
        captured["payload"] = payload

    monkeypatch.setattr(api, "exchange_gmail_oauth_code", fake_exchange)

    with TestClient(app) as client:
        response = client.get(f"/connect/gmail/callback?code=abc123&state={state}")

    assert response.status_code == 200
    assert response.json() == {
        "status": "connected",
        "agent_instance_id": "ceo-email-agent",
        "user_id": "owner@example.com",
        "mailbox_identity": "ceo@example.com",
    }
    assert captured["code"] == "abc123"
    assert captured["payload"]["agent_instance_id"] == "ceo-email-agent"


def test_exchange_code_rejects_mailbox_mismatch(monkeypatch, tmp_path):
    """exchange_code_for_token must raise when authorized email != expected mailbox."""
    import src.gmail_oauth as oauth

    monkeypatch.setattr(settings, "gmail_oauth_state_secret", "unit-state-secret")
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(oauth, "Flow", _FakeFlow)

    fake_service = MagicMock()
    fake_service.users.return_value.getProfile.return_value.execute.return_value = {
        "emailAddress": "other@example.com"
    }
    monkeypatch.setattr(oauth, "_build_service", lambda *a, **kw: fake_service)

    state = build_state("owner@example.com", "ceo-email-agent", mailbox_identity="ceo@example.com")
    payload = validate_state(state)

    with pytest.raises(ValueError, match="authorized.*other@example.com.*expected.*ceo@example.com"):
        oauth.exchange_code_for_token("abc", payload)


def test_exchange_code_skips_mailbox_check_when_no_identity(monkeypatch, tmp_path):
    """exchange_code_for_token skips mailbox verification when mailbox_identity is empty."""
    import src.gmail_oauth as oauth

    monkeypatch.setattr(settings, "gmail_oauth_state_secret", "unit-state-secret")
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "tokens"))
    monkeypatch.setattr(oauth, "Flow", _FakeFlow)

    state = build_state("owner@example.com", "default-email-agent", mailbox_identity="")
    payload = validate_state(state)

    # No _build_service mock — would raise if called; test verifies it isn't called.
    path = oauth.exchange_code_for_token("abc", payload)
    assert path.exists()
    assert json.loads(path.read_text()) == {"token": "oauth-token"}


def test_revoke_gmail_token_calls_google_revoke_endpoint(monkeypatch, tmp_path):
    """revoke_gmail_token POSTs to Google's revocation endpoint before deleting."""
    import src.gmail_oauth as oauth
    from src.gmail_oauth import revoke_gmail_token

    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "tokens"))

    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"refresh_token": "rt-abc123", "token": "at-xyz"}))

    revoke_calls = []

    class _FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout=None):
        revoke_calls.append({
            "url": req.full_url,
            "data": urllib.parse.parse_qs(req.data.decode()),
        })
        return _FakeResponse()

    monkeypatch.setattr(oauth.urllib.request, "urlopen", fake_urlopen)

    result = revoke_gmail_token()

    assert result is True
    assert len(revoke_calls) == 1
    assert revoke_calls[0]["url"] == "https://oauth2.googleapis.com/revoke"
    assert revoke_calls[0]["data"]["token"] == ["rt-abc123"]
    assert not token_path.exists()


def test_revoke_gmail_token_returns_false_when_no_token(monkeypatch, tmp_path):
    """revoke_gmail_token returns False silently when no token file exists."""
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "nonexistent.json"))
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "tokens"))

    from src.gmail_oauth import revoke_gmail_token
    assert revoke_gmail_token() is False


def test_revoke_gmail_token_deletes_locally_even_if_google_fails(monkeypatch, tmp_path):
    """Local token deleted even when the Google revocation HTTP call fails."""
    import src.gmail_oauth as oauth
    from src.gmail_oauth import revoke_gmail_token

    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "tokens"))

    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"refresh_token": "rt-abc", "token": "at-xyz"}))

    def fail_urlopen(req, timeout=None):
        raise OSError("network unreachable")

    monkeypatch.setattr(oauth.urllib.request, "urlopen", fail_urlopen)

    result = revoke_gmail_token()
    assert result is True
    assert not token_path.exists()
