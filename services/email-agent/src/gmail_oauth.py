from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Any

from src.config import SERVICE_ROOT, settings
from src.gmail_client import GMAIL_SCOPES
from src.token_store import prepared_token_file
from src.tenant import normalize_agent_instance_id, normalize_user_id

try:  # pragma: no cover - exercised through monkeypatched fakes in unit tests.
    from google_auth_oauthlib.flow import Flow
except ImportError:  # pragma: no cover
    Flow = None  # type: ignore[assignment]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _state_secret() -> str:
    secret = settings.gmail_oauth_state_secret or settings.token_encryption_key
    if not secret:
        raise RuntimeError("GMAIL_OAUTH_STATE_SECRET or AGENT_TOKEN_ENCRYPTION_KEY is required")
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


def exchange_code_for_token(code: str, state_payload: dict[str, Any]) -> Path:
    flow = _flow()
    flow.fetch_token(code=code)
    token_json = flow.credentials.to_json()
    user_id = state_payload["user_id"]
    agent_instance_id = state_payload["agent_instance_id"]
    with prepared_token_file(user_id, agent_instance_id) as token_path:
        path = Path(token_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token_json)
    return path
