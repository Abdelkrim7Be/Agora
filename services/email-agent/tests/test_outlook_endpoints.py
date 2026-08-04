"""The Outlook connect flow and the mailbox test probe, through the API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src import api
from src.api import app
from src.config import settings
from tests.conftest import patch_provider

HEADERS = {"X-Agora-User": "owner", "X-Agora-Agent-Instance": "sales-email-agent"}


def _azure_configured(monkeypatch):
    monkeypatch.setattr(settings, "outlook_client_id", "client-abc")
    monkeypatch.setattr(settings, "outlook_client_secret", "secret-xyz")
    monkeypatch.setattr(settings, "gmail_oauth_state_secret", "unit-state-secret")


def test_connect_start_returns_a_microsoft_authorization_url(monkeypatch):
    _azure_configured(monkeypatch)

    with TestClient(app) as client:
        response = client.get(
            "/agent-instances/sales-email-agent/connect/outlook/start", headers=HEADERS
        )

    assert response.status_code == 200
    body = response.json()
    assert body["authorization_url"].startswith("https://login.microsoftonline.com/")
    assert body["agent_instance_id"] == "sales-email-agent"
    assert "Mail.Send" in body["scopes"]
    assert "offline_access" in body["scopes"]


def test_connect_start_reports_missing_azure_config_as_503(monkeypatch):
    monkeypatch.setattr(settings, "outlook_client_id", "")
    monkeypatch.setattr(settings, "gmail_oauth_state_secret", "unit-state-secret")

    with TestClient(app) as client:
        response = client.get(
            "/agent-instances/sales-email-agent/connect/outlook/start", headers=HEADERS
        )

    assert response.status_code == 503
    assert "OUTLOOK_CLIENT_ID" in response.json()["detail"]


def test_callback_records_the_provider_before_anything_reads_the_mailbox(monkeypatch):
    """The provider setting has to land first.

    Every later call resolves through get_provider(), which defaults to Gmail —
    so an instance that connected Outlook but was still marked "gmail" would go
    looking for a Gmail token that was never stored.
    """
    _azure_configured(monkeypatch)
    monkeypatch.setattr(settings, "setup_enabled", False)
    recorded: list[tuple] = []

    monkeypatch.setattr(api, "exchange_outlook_oauth_code", lambda code, payload, state="": None)
    monkeypatch.setattr(
        api, "set_mail_provider", lambda provider, instance_id=None: recorded.append((provider, instance_id))
    )
    monkeypatch.setattr(api, "record_sync_success", lambda *a, **kw: None)

    state = api.build_outlook_oauth_state("owner", "sales-email-agent")
    with TestClient(app) as client:
        response = client.get(
            f"/connect/outlook/callback?code=abc&state={state}", follow_redirects=False
        )

    assert response.status_code == 303
    assert "outlook=" not in response.headers["location"]
    assert "/oauth/outlook/callback" in response.headers["location"]
    assert "gmail=connected" in response.headers["location"]
    assert recorded == [("outlook", "sales-email-agent")]


def test_callback_without_code_or_state_redirects_with_an_error():
    with TestClient(app) as client:
        response = client.get("/connect/outlook/callback", follow_redirects=False)

    assert response.status_code == 303
    assert "gmail=error" in response.headers["location"]


def test_callback_surfaces_a_rejected_exchange_as_an_error_redirect(monkeypatch):
    _azure_configured(monkeypatch)

    def rejected(code, payload, state=""):
        raise ValueError("Microsoft rejected the authorization code (invalid_grant).")

    monkeypatch.setattr(api, "exchange_outlook_oauth_code", rejected)

    state = api.build_outlook_oauth_state("owner", "sales-email-agent")
    with TestClient(app) as client:
        response = client.get(
            f"/connect/outlook/callback?code=abc&state={state}", follow_redirects=False
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "gmail=error" in location
    assert "invalid_grant" in location


def test_disconnect_removes_the_stored_token(monkeypatch):
    monkeypatch.setattr(api, "revoke_outlook_token", lambda user_id, instance_id: True)

    with TestClient(app) as client:
        response = client.post("/disconnect/outlook", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["disconnected"] is True


def test_connect_test_reports_a_reachable_mailbox(monkeypatch):
    provider = patch_provider(
        monkeypatch, api, probe=lambda: {"ok": True, "mailbox": "sales@acme.fr", "error": ""}
    )
    provider.name = "outlook"
    recorded: list[str] = []
    monkeypatch.setattr(api, "record_sync_success", lambda mode, *a, **kw: recorded.append(mode))

    with TestClient(app) as client:
        response = client.post("/connect/test", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "provider": "outlook",
        "mailbox": "sales@acme.fr",
        "error": "",
    }
    assert recorded == ["probe"]


def test_connect_test_reports_the_failure_reason_without_a_500(monkeypatch):
    """A broken mailbox is a result, not an error — the UI has to render why."""
    provider = patch_provider(
        monkeypatch,
        api,
        probe=lambda: {"ok": False, "mailbox": "", "error": "RefreshError: invalid_grant"},
    )
    provider.name = "gmail"
    failures: list[str] = []
    monkeypatch.setattr(api, "record_sync_failure", lambda error, *a, **kw: failures.append(error))

    with TestClient(app) as client:
        response = client.post("/connect/test", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "RefreshError: invalid_grant"
    assert failures == ["RefreshError: invalid_grant"]


def test_sync_status_carries_the_provider(monkeypatch):
    monkeypatch.setattr(api, "get_mail_provider", lambda instance_id: "outlook")
    monkeypatch.setattr(api, "get_sync_status", lambda *a, **kw: {"connection_status": "connected"})

    with TestClient(app) as client:
        response = client.get("/sync/status", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"connection_status": "connected", "provider": "outlook"}
