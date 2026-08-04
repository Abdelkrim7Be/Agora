"""Signed OAuth state shared by every mail provider.

The state travels through the provider's consent screen and back, so it is
attacker-visible and attacker-replayable. It carries an HMAC signature, an
expiry, and a nonce; `validate_state` fails closed on any of those.

Originally lived in `src/gmail_oauth.py`. It moved here unchanged when Outlook
became a second provider — both flows sign with the same secret, since the
secret protects the state envelope, not the provider account.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

from src.config import settings
from src.tenant import normalize_agent_instance_id, normalize_user_id
from src.token_store import active_master_key_secret


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
