from __future__ import annotations

import logging

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


async def classify_content(content: str, known_internal: bool = False) -> dict:
    """Ask the security service for a full trust classification of some content.

    /sanitize runs a fast path: with no heuristic keyword hit it returns
    UNTRUSTED without consulting the quarantined classifier, which keeps ordinary
    mail off a slow local model. That is the right default for throughput, but it
    means a carefully worded injection carrying none of the obvious phrases is
    never actually classified. This endpoint always runs the classifier, so the
    caller can pay for one where it matters — before a drafted reply becomes
    approvable — rather than on every message.

    Fail-safe like sanitize_email: an outage reports classifier_unavailable
    rather than an implicit pass.
    """
    payload = {"source": "gmail_thread", "content": content, "known_internal": known_internal}
    try:
        async with httpx.AsyncClient(timeout=settings.security_timeout) as client:
            resp = await client.post(f"{settings.security_url}/classify", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {
            "source": "gmail_thread",
            "trust": "UNTRUSTED",
            "reasons": ["security_service_unreachable"],
            "classifier_unavailable": True,
        }


def _record_quarantine_usage(usage: dict | None, node: str = "quarantine") -> None:
    """Book the security service's model call against the platform's LLM budget.

    That service runs a model on every inbound message but keeps no cost store of
    its own, so its tokens were missing from the costs view entirely — and it was
    the heavier of the two consumers. It reports what it spent; this side, which
    owns the ledger, writes it down. Never raises: accounting must not be able to
    fail a security decision.
    """
    if not usage:
        return
    try:
        from src.cost_tracker import compute_cost, record_cost

        model = str(usage.get("model") or "unknown")
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        if not input_tokens and not output_tokens:
            return
        record_cost({
            "node": node,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_eur": compute_cost(model, input_tokens, output_tokens),
        })
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("could not record quarantine model usage: %s", exc)


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
            verdict = resp.json()
            _record_quarantine_usage(verdict.get("usage"))
            return verdict
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
    recipients: list[str] | None = None,
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
        # Recipients are resolved from trusted context, not tool arguments, so
        # they have to be stated explicitly for recipient policy to see them.
        "recipients": list(recipients or []),
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


def sanitize_memory_write(namespace_label: str, content: str) -> dict:
    """POST a synthesized preference update to /sanitize before it is persisted.

    update_memory() synthesizes this text from an LLM call over the full run
    transcript, which includes untrusted email content — a successful prompt
    injection can smuggle instructions into what looks like "learned
    preferences", and those persist across every future run. Reusing /sanitize
    here checks the synthesized text itself for injection before it is written,
    the same way inbound email content is checked before the agent sees it.

    Fail closed: any failure blocks the write so a security-service outage
    never becomes a silent persistent-memory poisoning vector.
    """
    payload = {"sender": "", "subject": f"memory:{namespace_label}", "content": content}
    try:
        with httpx.Client(timeout=settings.security_timeout) as client:
            resp = client.post(f"{settings.security_url}/sanitize", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        return {
            "classification": "malicious",
            "injection_detected": True,
            "spam": False,
            "reasons": ["security_service_unreachable"],
            "cleaned_text": content,
            "classifier_unavailable": True,
            "source_trust": "UNTRUSTED",
            "fields": {},
        }
