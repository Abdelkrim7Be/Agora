from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from src.config import SERVICE_ROOT, settings
from src.gmail_client import GMAIL_SCOPES
from src.token_store import active_master_key_secret, delete_token, prepared_token_file
from src.tenant import normalize_agent_instance_id, normalize_user_id

try:  # pragma: no cover - exercised through monkeypatched fakes in unit tests.
    from google_auth_oauthlib.flow import Flow
except ImportError:  # pragma: no cover
    Flow = None  # type: ignore[assignment]

try:  # pragma: no cover
    from googleapiclient.discovery import build as _build_service
except ImportError:  # pragma: no cover
    _build_service = None  # type: ignore[assignment]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _state_secret() -> str:
    secret = settings.gmail_oauth_state_secret or active_master_key_secret() or settings.token_encryption_key
    if not secret:
        raise RuntimeError("GMAIL_OAUTH_STATE_SECRET or a token encryption key is required")
    return secret


def sign_state(payload: dict[str, Any]) -> str:
    body = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_state_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url(sig)}"


def validate_state(state: str, expected_agent_instance_id: str | None = None) -> dict[str, Any]:
    try:
        body, sig = state.split(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid OAuth state") from exc
    expected = _b64url(hmac.new(_state_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise ValueError("Invalid OAuth state")
    try:
        payload = json.loads(_unb64url(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid OAuth state") from exc
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("OAuth state expired")
    if expected_agent_instance_id and payload.get("agent_instance_id") != expected_agent_instance_id:
        raise ValueError("OAuth state instance mismatch")
    return payload


def build_state(user_id: str, agent_instance_id: str, mailbox_identity: str | None = None, ttl_seconds: int = 600) -> str:
    payload = {
        "tenant": normalize_user_id(user_id),
        "user_id": normalize_user_id(user_id),
        "agent_instance_id": normalize_agent_instance_id(agent_instance_id),
        "mailbox_identity": mailbox_identity or "",
        "nonce": uuid.uuid4().hex,
        "exp": int(time.time()) + ttl_seconds,
    }
    return sign_state(payload)


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


def exchange_code_for_token(code: str, state_payload: dict[str, Any]) -> Path:
    flow = _flow()
    flow.fetch_token(code=code)

    # When the OAuth state carried an explicit mailbox identity, verify the account
    # Google actually authorized matches. Fail closed — never store a token for the
    # wrong mailbox. Skip the check when no mailbox was specified (legacy single-user).
    expected_mailbox = (state_payload.get("mailbox_identity") or "").strip().lower()
    if expected_mailbox:
        _verify_mailbox(flow.credentials, expected_mailbox)

    token_json = flow.credentials.to_json()
    user_id = state_payload["user_id"]
    agent_instance_id = state_payload["agent_instance_id"]
    with prepared_token_file(user_id, agent_instance_id) as token_path:
        path = Path(token_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token_json)
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
    from src.token_store import token_file_for_user

    target = token_file_for_user(user_id, agent_instance_id)
    enc_path = target.with_name(target.name + ".enc")
    if not target.is_file() and not enc_path.is_file():
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
