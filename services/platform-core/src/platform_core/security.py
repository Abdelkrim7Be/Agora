from __future__ import annotations

import logging
from typing import Callable

import httpx

logger = logging.getLogger(__name__)


class SecurityClient:
    """HTTP client for the security service's sanitize/authorize/audit surface.

    Every method returns a verdict rather than raising, and the fallback is
    chosen per endpoint: reads degrade open (a listing is not a decision) while
    anything gating an action degrades closed, so an outage can never widen what
    the agent is allowed to do.
    """

    def __init__(
        self,
        *,
        base_url: Callable[[], str],
        timeout: Callable[[], float],
        current_user_id: Callable[[], str],
        current_agent_instance_id: Callable[[], str],
        record_usage: Callable[[dict | None, str], None] | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._current_user_id = current_user_id
        self._current_agent_instance_id = current_agent_instance_id
        self._record_usage = record_usage

    def _url(self, path: str) -> str:
        return f"{self._base_url()}{path}"

    async def classify_content(self, content: str, known_internal: bool = False) -> dict:
        """Always run the quarantined classifier over some content.

        /sanitize runs a fast path: with no heuristic keyword hit it returns
        UNTRUSTED without consulting the classifier, which keeps ordinary mail
        off a slow model. That is the right default for throughput, but it means
        a carefully worded injection carrying none of the obvious phrases is
        never actually classified. This endpoint always runs it, so a caller can
        pay for one where it matters — before a drafted reply becomes approvable.
        """
        payload = {"source": "gmail_thread", "content": content, "known_internal": known_internal}
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                resp = await client.post(self._url("/classify"), json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return {
                "source": "gmail_thread",
                "trust": "UNTRUSTED",
                "reasons": ["security_service_unreachable"],
                "classifier_unavailable": True,
            }

    def record_quarantine_usage(self, usage: dict | None, node: str = "quarantine") -> None:
        """Book the security service's model call against the platform's budget.

        That service runs a model on every inbound message but keeps no cost
        store of its own. It reports what it spent; this side, which owns the
        ledger, writes it down. Never raises: accounting must not be able to
        fail a security decision.
        """
        if not usage or self._record_usage is None:
            return
        try:
            self._record_usage(usage, node)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("could not record quarantine model usage: %s", exc)

    async def sanitize_email(self, sender: str, subject: str, content: str) -> dict:
        """Sanitize untrusted email content before the agent graph sees it.

        Fail-safe: any failure returns a cautious verdict with
        classifier_unavailable=True so an outage can never become a silent
        unsanitized passthrough. cleaned_text falls back to the original.
        """
        payload = {"sender": sender, "subject": subject, "content": content}
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                resp = await client.post(self._url("/sanitize"), json=payload)
                resp.raise_for_status()
                verdict = resp.json()
                self.record_quarantine_usage(verdict.get("usage"))
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

    async def fetch_policy(self) -> dict:
        """Read the active capability policy, for the control panel.

        Degrades open: this is a read-only view, not a security decision, so an
        outage returns empty yaml and an error flag rather than a 5xx.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                resp = await client.get(self._url("/policy"))
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return {"policy_yaml": "", "error": "security_service_unreachable"}

    def authorize_payload(
        self,
        action: str,
        args: dict,
        run_id: str,
        action_id: str = "",
        arg_trust: dict | None = None,
        recipients: list[str] | None = None,
    ) -> dict:
        """Build the /authorize request body.

        Tenant identity comes from the ambient scope, never from `args` — the
        arguments are model output, and a model that could name its own tenant
        could authorize itself against someone else's policy.
        """
        context = {
            "run_id": run_id,
            "user_id": self._current_user_id(),
            "agent_instance_id": self._current_agent_instance_id(),
        }
        if action_id:
            context["action_id"] = action_id
        return {
            "action": action,
            "args": args,
            "context": context,
            "arg_trust": arg_trust or {},
            # Recipients are resolved from trusted context, not tool arguments, so
            # they have to be stated explicitly for recipient policy to see them.
            "recipients": list(recipients or []),
        }

    def authorize_action(
        self,
        action: str,
        args: dict,
        run_id: str,
        action_id: str = "",
        arg_trust: dict | None = None,
        recipients: list[str] | None = None,
    ) -> dict:
        """Authorize a proposed tool action.

        Fail closed: any failure denies, so an outage never becomes an unguarded
        tool execution.
        """
        payload = self.authorize_payload(
            action, args, run_id, action_id, arg_trust, recipients
        )
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                resp = client.post(self._url("/authorize"), json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return {"decision": "deny", "reason": "security_service_unreachable"}

    def audit_output(self, action: str, to: str, subject: str, content: str, run_id: str) -> dict:
        """Audit outbound content immediately before a send-type tool executes.

        Runs after /authorize and any HITL approval or edit, so it sees whatever
        is truly about to leave the system — drafted or human-edited. Fail
        closed: any failure flags the send.
        """
        payload = {
            "action": action,
            "to": to,
            "subject": subject,
            "content": content,
            "context": {"run_id": run_id},
        }
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                resp = client.post(self._url("/audit-output"), json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return {
                "flagged": True,
                "reasons": ["security_service_unreachable"],
                "classifier_unavailable": True,
            }

    def sanitize_memory_write(self, namespace_label: str, content: str) -> dict:
        """Check a synthesized preference update before it is persisted.

        Learned preferences are synthesized by a model reading the full run
        transcript, which includes untrusted email content — an injection can
        smuggle instructions into what looks like a preference, and those
        persist across every future run.

        Fail closed: any failure blocks the write, so an outage never becomes a
        silent persistent-memory poisoning vector.
        """
        payload = {"sender": "", "subject": f"memory:{namespace_label}", "content": content}
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                resp = client.post(self._url("/sanitize"), json=payload)
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
