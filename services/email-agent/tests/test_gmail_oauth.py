from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from src.api import app
from src.config import settings
from src.gmail_oauth import build_state


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
