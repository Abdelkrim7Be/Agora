from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from src.config import SERVICE_ROOT, settings
from src.gmail_client import GMAIL_SCOPES
from src.oauth_state import build_state, sign_state, validate_state  # noqa: F401 — re-exported
from src.token_store import (
    delete_token,
    has_stored_token,
    prepared_token_file,
)

try:  # pragma: no cover - exercised through monkeypatched fakes in unit tests.
    from google_auth_oauthlib.flow import Flow
except ImportError:  # pragma: no cover
    Flow = None  # type: ignore[assignment]

try:  # pragma: no cover
    from googleapiclient.discovery import build as _build_service
except ImportError:  # pragma: no cover
    _build_service = None  # type: ignore[assignment]


def _flow():
    if Flow is None:
        raise RuntimeError("google-auth-oauthlib is required for Gmail OAuth onboarding")
    return Flow.from_client_secrets_file(
        str(SERVICE_ROOT / settings.gmail_credentials_path),
        scopes=GMAIL_SCOPES,
        redirect_uri=settings.gmail_oauth_redirect_uri,
        autogenerate_code_verifier=False,
    )


def build_authorization_url(state: str) -> str:
    flow = _flow()
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        state=state,
    )
    return authorization_url


def _verify_mailbox(credentials: Any, expected_mailbox: str) -> str:
    """Return the actual authorized email, raising if it does not match expected.

    Fails closed: if the profile call itself fails we raise rather than storing
    a token for an unverified mailbox identity.
    """
    if _build_service is None:
        raise RuntimeError("google-api-python-client is required for mailbox verification")
    try:
        service = _build_service("gmail", "v1", credentials=credentials)
        profile = service.users().getProfile(userId="me").execute()
        actual = (profile.get("emailAddress") or "").strip().lower()
    except Exception as exc:
        raise ValueError(f"Could not verify authorized mailbox: {exc}") from exc
    if actual != expected_mailbox:
        raise ValueError(
            f"OAuth authorized '{actual}' but expected '{expected_mailbox}'. "
            "Re-authorize with the correct Google account."
        )
    return actual


def _explain_token_fetch_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "invalid_grant" in lowered:
        return (
            "Google rejected the authorization code (invalid_grant). The code was "
            "already used or expired — close the window and click Connect Gmail again."
        )
    if "redirect_uri_mismatch" in lowered:
        return (
            "Redirect URI mismatch: the configured GMAIL_OAUTH_REDIRECT_URI is not "
            "listed in the Google Cloud OAuth client. Fix the authorized redirect URIs."
        )
    if "invalid_client" in lowered or "unauthorized_client" in lowered:
        return (
            "Google rejected the OAuth client (invalid_client). credentials.json does "
            "not match the client configured in Google Cloud Console."
        )
    if "access_denied" in lowered:
        return "Google reported access_denied — the account refused consent or is not a test user of the OAuth app."
    return f"Token exchange with Google failed: {text}"


def exchange_code_for_token(code: str, state_payload: dict[str, Any]) -> Path:
    agent_instance_id = state_payload["agent_instance_id"]
    flow = _flow()
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        print(f"oauth: token exchange failed for {agent_instance_id}: {exc!r}")
        raise ValueError(_explain_token_fetch_error(exc)) from exc

    # When the OAuth state carried an explicit mailbox identity, verify the account
    # Google actually authorized matches. Fail closed — never store a token for the
    # wrong mailbox. Skip the check when no mailbox was specified (legacy single-user).
    expected_mailbox = (state_payload.get("mailbox_identity") or "").strip().lower()
    if expected_mailbox:
        _verify_mailbox(flow.credentials, expected_mailbox)

    token_json = flow.credentials.to_json()
    user_id = state_payload["user_id"]
    try:
        with prepared_token_file(user_id, agent_instance_id) as token_path:
            path = Path(token_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token_json)
    except Exception as exc:
        print(f"oauth: token persistence failed for {agent_instance_id}: {exc!r}")
        raise RuntimeError(
            f"Gmail authorized but the token could not be stored ({exc}). "
            "Check the token store volume and encryption key, then reconnect."
        ) from exc
    if not getattr(flow.credentials, "refresh_token", "unknown"):
        print(
            f"oauth: WARNING — no refresh_token returned for {agent_instance_id}; "
            "the connection will drop when the access token expires"
        )
    print(f"oauth: token stored for {agent_instance_id}")
    return path


def revoke_gmail_token(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> bool:
    """Revoke the Gmail OAuth token at Google then delete the local token file.

    Best-effort: a network error during revocation is logged but does not block
    the local deletion. Returns True if a token file was found, False otherwise.
    Always deletes the local file regardless of revocation outcome.
    """
    # Read the token value before deleting, using the prepared_token_file context
    # to transparently handle at-rest encryption.
    if not has_stored_token(agent_instance_id):
        return False

    revoke_value: str | None = None
    try:
        with prepared_token_file(user_id, agent_instance_id) as token_path:
            raw = Path(token_path).read_text()
            data = json.loads(raw)
            revoke_value = data.get("refresh_token") or data.get("token")
    except Exception as exc:
        print(f"token: could not read token for revocation: {exc}")

    # Attempt Google-side revocation — best-effort.
    if revoke_value:
        try:
            body = urllib.parse.urlencode({"token": revoke_value}).encode()
            req = urllib.request.Request(
                "https://oauth2.googleapis.com/revoke",
                data=body,
                method="POST",
            )
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception as exc:
            print(f"token: Google revocation request failed (local token still deleted): {exc}")

    delete_token(user_id, agent_instance_id)
    return True
