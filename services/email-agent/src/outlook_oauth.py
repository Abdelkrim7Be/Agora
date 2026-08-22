"""Microsoft identity platform OAuth for Outlook mailboxes.

Mirrors `src/gmail_oauth.py` one-for-one — same signed state from
`src.oauth_state`, same fail-closed mailbox verification, same encrypted token
envelope through `src.token_store` — so the two connect flows behave
identically from the caller's point of view.

The authorization-code exchange is done directly over `httpx` (already a
dependency) rather than through `msal`. The flow is four form fields; pulling in
an SDK to send them would add a dependency without removing any of the failure
handling we have to write anyway.

PKCE is always used. The redirect lands on the gateway, which forwards to the
agent, so the code is briefly visible to anything sitting on that path; the
verifier makes an intercepted code unusable on its own.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from src.config import settings
from src.connected_mailboxes import claim_mailbox, release_mailbox
from src.oauth_state import build_state, sign_state, validate_state  # noqa: F401 — re-exported
from src.token_store import delete_token, has_stored_token, prepared_token_file

logger = logging.getLogger(__name__)

PROVIDER = "outlook"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# offline_access is what returns a refresh token; without it the connection dies
# at the first access-token expiry.
OUTLOOK_SCOPES = [
    "offline_access",
    "User.Read",
    "Mail.ReadWrite",
    "Mail.Send",
]

# PKCE verifiers are minted at authorization time and needed again at callback
# time, in a different request. They are single-use and short-lived, so they are
# held in memory keyed by the state signature rather than persisted.
_PKCE_VERIFIERS: dict[str, str] = {}
_PKCE_MAX_PENDING = 64


def _require_client_config() -> tuple[str, str]:
    client_id = settings.outlook_client_id.strip()
    client_secret = settings.outlook_client_secret.strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "OUTLOOK_CLIENT_ID and OUTLOOK_CLIENT_SECRET are required to connect an Outlook mailbox"
        )
    return client_id, client_secret


def _authority() -> str:
    return f"https://login.microsoftonline.com/{settings.outlook_tenant}/oauth2/v2.0"


def _new_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _remember_verifier(state: str, verifier: str) -> None:
    # Bound the map so an unfinished-flow loop cannot grow it without limit.
    if len(_PKCE_VERIFIERS) >= _PKCE_MAX_PENDING:
        _PKCE_VERIFIERS.pop(next(iter(_PKCE_VERIFIERS)), None)
    _PKCE_VERIFIERS[state] = verifier


def build_authorization_url(state: str) -> str:
    client_id, _ = _require_client_config()
    verifier, challenge = _new_pkce_pair()
    _remember_verifier(state, verifier)
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": settings.outlook_oauth_redirect_uri,
            "response_mode": "query",
            "scope": " ".join(OUTLOOK_SCOPES),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            # Force the account chooser so connecting a second mailbox does not
            # silently reuse the browser's current Microsoft session.
            "prompt": "select_account",
        }
    )
    return f"{_authority()}/authorize?{query}"


def _explain_token_fetch_error(payload: dict[str, Any], status: int) -> str:
    code = str(payload.get("error") or "").lower()
    detail = str(payload.get("error_description") or "").strip()
    if code == "invalid_grant":
        return (
            "Microsoft rejected the authorization code (invalid_grant). The code was "
            "already used or expired — close the window and click Connect again."
        )
    if code == "redirect_uri_mismatch":
        return (
            "Redirect URI mismatch: OUTLOOK_OAUTH_REDIRECT_URI is not listed in the "
            "Azure app registration. Fix the redirect URIs there."
        )
    if code in ("invalid_client", "unauthorized_client"):
        return (
            "Microsoft rejected the OAuth client (invalid_client). OUTLOOK_CLIENT_ID / "
            "OUTLOOK_CLIENT_SECRET do not match the Azure app registration, or the secret expired."
        )
    if code == "access_denied":
        return "Microsoft reported access_denied — consent was refused, or an admin has not granted the requested scopes."
    return f"Token exchange with Microsoft failed (HTTP {status}): {detail or code or 'unknown error'}"


def _post_token(form: dict[str, str]) -> dict[str, Any]:
    try:
        response = httpx.post(f"{_authority()}/token", data=form, timeout=20)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Microsoft to exchange the token: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        raise ValueError(_explain_token_fetch_error(payload, response.status_code))
    return payload


def _verify_mailbox(access_token: str, expected_mailbox: str | None = None) -> str:
    """Return the authorized mailbox address, raising if it is not the expected one.

    Fails closed, like the Gmail path: a token is never stored for a mailbox we
    could not confirm.
    """
    try:
        response = httpx.get(
            f"{GRAPH_BASE}/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        response.raise_for_status()
        profile = response.json()
    except Exception as exc:
        raise ValueError(f"Could not verify authorized mailbox: {exc}") from exc

    actual = (profile.get("mail") or profile.get("userPrincipalName") or "").strip().lower()
    expected = (expected_mailbox or "").strip().lower()
    if expected and actual != expected:
        raise ValueError(
            f"OAuth authorized '{actual}' but expected '{expected}'. "
            "Re-authorize with the correct Microsoft account."
        )
    return actual


def exchange_code_for_token(code: str, state_payload: dict[str, Any], state: str = "") -> Path:
    agent_instance_id = state_payload["agent_instance_id"]
    client_id, client_secret = _require_client_config()
    verifier = _PKCE_VERIFIERS.pop(state, "") if state else ""

    form = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": settings.outlook_oauth_redirect_uri,
        "scope": " ".join(OUTLOOK_SCOPES),
    }
    if verifier:
        form["code_verifier"] = verifier

    try:
        payload = _post_token(form)
    except ValueError as exc:
        logger.warning(f"oauth: outlook token exchange failed for {agent_instance_id}: {exc}")
        raise

    access_token = payload.get("access_token") or ""
    if not access_token:
        raise ValueError("Microsoft returned no access token.")

    expected_mailbox = (state_payload.get("mailbox_identity") or "").strip().lower()
    actual_mailbox = _verify_mailbox(access_token, expected_mailbox or None)

    if not payload.get("refresh_token"):
        logger.info(
            f"oauth: WARNING — no refresh_token returned for {agent_instance_id}; "
            "the connection will drop when the access token expires"
        )

    user_id = state_payload["user_id"]
    try:
        with prepared_token_file(user_id, agent_instance_id, provider=PROVIDER) as token_path:
            path = Path(token_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
    except Exception as exc:
        logger.warning(f"oauth: outlook token persistence failed for {agent_instance_id}: {exc!r}")
        raise RuntimeError(
            f"Outlook authorized but the token could not be stored ({exc}). "
            "Check the token store volume and encryption key, then reconnect."
        ) from exc
    try:
        claim_mailbox(PROVIDER, actual_mailbox, user_id, agent_instance_id)
    except Exception:
        delete_token(user_id, agent_instance_id, provider=PROVIDER)
        raise

    logger.info(f"oauth: outlook token stored for {agent_instance_id}")
    return path


def refresh_access_token(user_id: str | None, agent_instance_id: str | None) -> str:
    """Return a usable access token, refreshing and re-persisting when expired.

    Microsoft access tokens last about an hour, so the provider calls this
    before every request rather than caching across a poll cycle.
    """
    client_id, client_secret = _require_client_config()
    with prepared_token_file(user_id, agent_instance_id, provider=PROVIDER) as token_path:
        path = Path(token_path)
        try:
            stored = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                "No usable Outlook token is stored for this instance — reconnect the mailbox."
            ) from exc

        refresh_token = stored.get("refresh_token") or ""
        if not refresh_token:
            raise RuntimeError(
                "The stored Outlook token has no refresh token — reconnect the mailbox."
            )

        payload = _post_token(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(OUTLOOK_SCOPES),
            }
        )
        access_token = payload.get("access_token") or ""
        if not access_token:
            raise RuntimeError("Microsoft returned no access token on refresh.")

        # Microsoft rotates refresh tokens; keep the new one or the next refresh fails.
        merged = {**stored, **payload}
        path.write_text(json.dumps(merged))
        return access_token


def revoke_outlook_token(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> bool:
    """Delete the stored Outlook token for an instance.

    Microsoft has no per-token revocation endpoint equivalent to Google's — a
    refresh token is invalidated from the account's app-consent page or by
    revoking sessions directory-side. Deleting our copy is what disconnects the
    instance; the surviving grant is documented as a known caveat.
    """
    if not has_stored_token(agent_instance_id, provider=PROVIDER):
        return False
    delete_token(user_id, agent_instance_id, provider=PROVIDER)
    release_mailbox(PROVIDER, agent_instance_id)
    return True
