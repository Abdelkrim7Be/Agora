"""Outlook OAuth: state, PKCE, mailbox verification, token storage.

No network. Every Microsoft endpoint is a stub — these assert the flow's
decisions (what gets stored, what gets refused), not Microsoft's behaviour.
"""

from __future__ import annotations

import json

import pytest

from src import outlook_oauth
from src.config import settings
from src.token_store import has_stored_token, token_file_for_user, token_scope


@pytest.fixture(autouse=True)
def azure_app(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "outlook_client_id", "client-abc")
    monkeypatch.setattr(settings, "outlook_client_secret", "secret-xyz")
    monkeypatch.setattr(settings, "outlook_tenant", "common")
    monkeypatch.setattr(
        settings, "outlook_oauth_redirect_uri", "https://gateway.example/api/agent/connect/outlook/callback"
    )
    monkeypatch.setattr(settings, "gmail_oauth_state_secret", "unit-state-secret")
    monkeypatch.setattr(settings, "token_encryption_key", "")
    monkeypatch.setattr(settings, "token_encryption_key_file", "")
    monkeypatch.setattr(settings, "token_store_backend", "file")
    monkeypatch.setattr(settings, "gmail_token_path", str(tmp_path / "token.json"))
    monkeypatch.setattr(settings, "gmail_token_store_path", str(tmp_path / "tokens.json"))
    outlook_oauth._PKCE_VERIFIERS.clear()
    yield


def _state():
    return outlook_oauth.build_state("owner@example.com", "sales-email-agent", mailbox_identity="sales@example.com")


def test_authorization_url_carries_pkce_and_scopes():
    state = _state()

    url = outlook_oauth.build_authorization_url(state)

    assert url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")
    assert "code_challenge_method=S256" in url
    assert "code_challenge=" in url
    assert "offline_access" in url and "Mail.Send" in url
    assert "prompt=select_account" in url
    # The verifier is kept for the callback leg, never put in the URL.
    verifier = outlook_oauth._PKCE_VERIFIERS[state]
    assert verifier not in url


def test_missing_azure_credentials_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "outlook_client_id", "")
    with pytest.raises(RuntimeError, match="OUTLOOK_CLIENT_ID"):
        outlook_oauth.build_authorization_url(_state())


def test_pending_verifier_map_is_bounded():
    for index in range(outlook_oauth._PKCE_MAX_PENDING + 10):
        outlook_oauth._remember_verifier(f"state-{index}", f"verifier-{index}")
    assert len(outlook_oauth._PKCE_VERIFIERS) <= outlook_oauth._PKCE_MAX_PENDING


def test_exchange_stores_the_token_under_the_outlook_scope(monkeypatch):
    state = _state()
    outlook_oauth.build_authorization_url(state)
    payload = outlook_oauth.validate_state(state)

    monkeypatch.setattr(
        outlook_oauth,
        "_post_token",
        lambda form: {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3599},
    )
    monkeypatch.setattr(outlook_oauth, "_verify_mailbox", lambda token, expected: expected)

    path = outlook_oauth.exchange_code_for_token("code-1", payload, state=state)

    assert json.loads(path.read_text())["refresh_token"] == "rt-1"
    assert has_stored_token("sales-email-agent", provider="outlook") is True
    # The Gmail slot for the same instance must be untouched.
    assert has_stored_token("sales-email-agent", provider="gmail") is False


def test_exchange_sends_the_code_verifier(monkeypatch):
    state = _state()
    outlook_oauth.build_authorization_url(state)
    expected_verifier = outlook_oauth._PKCE_VERIFIERS[state]
    payload = outlook_oauth.validate_state(state)
    seen = {}

    def capture(form):
        seen.update(form)
        return {"access_token": "at-1", "refresh_token": "rt-1"}

    monkeypatch.setattr(outlook_oauth, "_post_token", capture)
    monkeypatch.setattr(outlook_oauth, "_verify_mailbox", lambda token, expected: expected)

    outlook_oauth.exchange_code_for_token("code-1", payload, state=state)

    assert seen["code_verifier"] == expected_verifier
    assert seen["grant_type"] == "authorization_code"
    # Single use — a replayed callback must not resend the same verifier.
    assert state not in outlook_oauth._PKCE_VERIFIERS


def test_wrong_mailbox_refuses_to_store_a_token(monkeypatch):
    state = _state()
    payload = outlook_oauth.validate_state(state)
    monkeypatch.setattr(
        outlook_oauth, "_post_token", lambda form: {"access_token": "at-1", "refresh_token": "rt-1"}
    )

    def wrong_account(token, expected):
        raise ValueError(f"OAuth authorized 'other@example.com' but expected '{expected}'.")

    monkeypatch.setattr(outlook_oauth, "_verify_mailbox", wrong_account)

    with pytest.raises(ValueError, match="expected 'sales@example.com'"):
        outlook_oauth.exchange_code_for_token("code-1", payload, state=state)

    assert has_stored_token("sales-email-agent", provider="outlook") is False


def test_missing_access_token_is_rejected(monkeypatch):
    state = _state()
    payload = outlook_oauth.validate_state(state)
    monkeypatch.setattr(outlook_oauth, "_post_token", lambda form: {"token_type": "Bearer"})

    with pytest.raises(ValueError, match="no access token"):
        outlook_oauth.exchange_code_for_token("code-1", payload, state=state)


@pytest.mark.parametrize(
    "error,expected",
    [
        ("invalid_grant", "already used or expired"),
        ("redirect_uri_mismatch", "OUTLOOK_OAUTH_REDIRECT_URI"),
        ("invalid_client", "OUTLOOK_CLIENT_ID"),
        ("access_denied", "consent was refused"),
        ("something_else", "HTTP 400"),
    ],
)
def test_token_errors_are_explained(error, expected):
    message = outlook_oauth._explain_token_fetch_error({"error": error}, 400)
    assert expected in message


def test_refresh_rotates_and_persists_the_new_refresh_token(monkeypatch):
    state = _state()
    payload = outlook_oauth.validate_state(state)
    monkeypatch.setattr(
        outlook_oauth, "_post_token", lambda form: {"access_token": "at-1", "refresh_token": "rt-1"}
    )
    monkeypatch.setattr(outlook_oauth, "_verify_mailbox", lambda token, expected: expected)
    outlook_oauth.exchange_code_for_token("code-1", payload, state=state)

    monkeypatch.setattr(
        outlook_oauth, "_post_token", lambda form: {"access_token": "at-2", "refresh_token": "rt-2"}
    )
    token = outlook_oauth.refresh_access_token("owner@example.com", "sales-email-agent")

    assert token == "at-2"
    stored = json.loads(
        token_file_for_user(agent_instance_id="sales-email-agent", provider="outlook").read_text()
    )
    assert stored["refresh_token"] == "rt-2"


def test_refresh_without_a_stored_token_is_actionable():
    with pytest.raises(RuntimeError, match="reconnect the mailbox"):
        outlook_oauth.refresh_access_token("owner@example.com", "never-connected")


def test_revoke_deletes_the_stored_token(monkeypatch):
    state = _state()
    payload = outlook_oauth.validate_state(state)
    monkeypatch.setattr(
        outlook_oauth, "_post_token", lambda form: {"access_token": "at-1", "refresh_token": "rt-1"}
    )
    monkeypatch.setattr(outlook_oauth, "_verify_mailbox", lambda token, expected: expected)
    outlook_oauth.exchange_code_for_token("code-1", payload, state=state)

    assert outlook_oauth.revoke_outlook_token("owner@example.com", "sales-email-agent") is True
    assert has_stored_token("sales-email-agent", provider="outlook") is False
    assert outlook_oauth.revoke_outlook_token("owner@example.com", "sales-email-agent") is False


def test_token_scope_keeps_gmail_paths_unchanged():
    """Existing Gmail tokens must not need a migration."""
    assert token_scope("sales-email-agent", "gmail") == "sales-email-agent"
    assert token_scope("sales-email-agent") == "sales-email-agent"
    assert token_scope("sales-email-agent", "outlook") == "sales-email-agent__outlook"


def test_the_two_providers_never_share_a_token_file():
    gmail_path = token_file_for_user(agent_instance_id="sales-email-agent", provider="gmail")
    outlook_path = token_file_for_user(agent_instance_id="sales-email-agent", provider="outlook")
    assert gmail_path != outlook_path
