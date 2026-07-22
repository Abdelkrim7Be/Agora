from __future__ import annotations

import httpx

from src.config import settings


async def sanitize_email(sender: str, subject: str, content: str) -> dict:
    """POST untrusted email content to the security service /sanitize endpoint.

    Fail-safe: any failure (connection refused, timeout, non-2xx) returns a
    cautious verdict with classifier_unavailable=True so an outage can never
    become a silent unsanitized passthrough. cleaned_text falls back to the
    original content.
    """
    payload = {"sender": sender, "subject": subject, "content": content}
    try:
        async with httpx.AsyncClient(timeout=settings.security_timeout) as client:
            resp = await client.post(f"{settings.security_url}/sanitize", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {
            "classification": "suspicious",
            "injection_detected": False,
            "spam": False,
            "reasons": ["security_service_unreachable"],
            "cleaned_text": content,
            "classifier_unavailable": True,
            "source_trust": "UNTRUSTED",
            "fields": {
                "sender": {"value": sender, "trust": "UNTRUSTED"},
                "subject": {"value": subject, "trust": "UNTRUSTED"},
                "body": {"value": content, "trust": "UNTRUSTED"},
            },
        }


async def fetch_policy() -> dict:
    """GET the active capability policy from the security service (for the control panel).

    Degrades gracefully: an outage returns empty yaml + an error flag rather than 5xx,
    since this is a read-only view, not a security decision.
    """
    try:
        async with httpx.AsyncClient(timeout=settings.security_timeout) as client:
            resp = await client.get(f"{settings.security_url}/policy")
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {"policy_yaml": "", "error": "security_service_unreachable"}


def authorize_action(
    action: str,
    args: dict,
    run_id: str,
    action_id: str = "",
    arg_trust: dict | None = None,
) -> dict:
    """POST a proposed tool action to the security service /authorize endpoint.

    Fail closed: any failure denies the action so a security-service outage never
    becomes an unguarded tool execution.
    """
    from src.tenant import current_agent_instance_id, current_user_id

    context = {
        "run_id": run_id,
        "user_id": current_user_id(),
        "agent_instance_id": current_agent_instance_id(),
    }
    if action_id:
        context["action_id"] = action_id
    payload = {
        "action": action,
        "args": args,
        "context": context,
        "arg_trust": arg_trust or {},
    }
    try:
        with httpx.Client(timeout=settings.security_timeout) as client:
            resp = client.post(f"{settings.security_url}/authorize", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {"decision": "deny", "reason": "security_service_unreachable"}


def audit_output(action: str, to: str, subject: str, content: str, run_id: str) -> dict:
    """POST outbound content to the security service /audit-output endpoint.

    This runs right before a send-type tool actually executes — after /authorize
    and any HITL approval/edit — so it catches leaked injected instructions in
    whatever content is truly about to leave the system, LLM-drafted or human-edited.
    Fail closed: any failure flags the send so a security-service outage never
    becomes an unaudited external send.
    """
    payload = {
        "action": action,
        "to": to,
        "subject": subject,
        "content": content,
        "context": {"run_id": run_id},
    }
    try:
        with httpx.Client(timeout=settings.security_timeout) as client:
            resp = client.post(f"{settings.security_url}/audit-output", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {
            "flagged": True,
            "reasons": ["security_service_unreachable"],
            "classifier_unavailable": True,
        }
