from __future__ import annotations

from datetime import datetime, timezone
import logging

from src.config import settings
from src.gmail_client import send_message
from src.roles import resolve_role

logger = logging.getLogger(__name__)

_ACTION_LABELS = {
    "write_email": "Brouillon de réponse",
    "reply_all": "Brouillon de réponse (tous)",
    "forward_email": "Transfert",
    "create_draft": "Brouillon",
    "trash_email": "Suppression",
}


def _resolve_recipient(workflow_approver: str | None, workflow_route_to: list[str] | None) -> str | None:
    """Same resolution order as the forward path (graph._resolve_route_target):
    a literal email wins as-is; otherwise resolve through the role directory.
    Never blasts to multiple recipients — one clear owner per notification."""
    candidates = [workflow_approver, *(workflow_route_to or [])]
    for candidate in candidates:
        if not candidate:
            continue
        cleaned = candidate.strip()
        if not cleaned:
            continue
        if "@" in cleaned:
            return cleaned.lower()
        resolved = resolve_role(cleaned)
        if resolved and resolved.primary_email:
            return resolved.primary_email
    return None


def _action_label(action_name: str | None, workflow_owner: str | None) -> str:
    label = _ACTION_LABELS.get(action_name or "", "Action")
    if workflow_owner:
        return f"{label} vers {workflow_owner}"
    return label


def _resolve_escalation_target(workflow_approver: str | None, workflow_owner: str | None) -> str | None:
    for candidate in (workflow_approver, workflow_owner):
        if not candidate:
            continue
        cleaned = candidate.strip()
        if not cleaned:
            continue
        if "@" in cleaned:
            return cleaned.lower()
        resolved = resolve_role(cleaned)
        if resolved and resolved.primary_email:
            return resolved.primary_email
    return None




def _resolve_admin_recipient(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if "@" in cleaned:
        return cleaned.lower()
    resolved = resolve_role(cleaned)
    if resolved and resolved.primary_email:
        return resolved.primary_email
    return None


def _send_system_notification(recipient: str | None, subject: str, body: str) -> bool:
    if not settings.notify_enabled or not recipient:
        return False
    try:
        send_message(to=recipient, subject=subject, body=body)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("notifications: failed to send system mail to %s: %s", recipient, exc)
        return False


def _run_reference(run_id: str) -> str:
    return (
        f"{settings.notify_app_base_url.rstrip('/')}/#run/{run_id}"
        if settings.notify_app_base_url
        else run_id
    )


def _format_overdue_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours} h {minutes} min"
    if hours:
        return f"{hours} h"
    return f"{minutes or 1} min"


def _pending_action_name(result: dict) -> str | None:
    interrupts = result.get("__interrupt__") or []
    if not interrupts:
        return None
    value = interrupts[0].value
    request = value[0] if isinstance(value, list) and value else value
    if not isinstance(request, dict):
        return None
    return (request.get("action_request") or {}).get("action")


def notify_pending_approval(run_id: str, email_input: dict, result: dict) -> None:
    """Email the responsible approver that a run is waiting for validation.

    System mail, not an agent-authored reply: it does not go through HITL (there is
    nothing to approve about the notification itself), never includes the email
    body (links back to the approval instead), and a send failure here must never
    break approval creation — hence the broad except at the end.
    """
    if not settings.notify_enabled:
        return
    try:
        recipient = _resolve_recipient(result.get("workflow_approver"), result.get("workflow_route_to"))
        if not recipient:
            return
        label = _action_label(_pending_action_name(result), result.get("workflow_owner"))
        subject = f"Action requise : {label}"
        reference = _run_reference(run_id)
        body = (
            f"Une action est en attente de validation : {label} pour l'email "
            f"« {email_input.get('subject') or '(sans objet)'} » de "
            f"{email_input.get('author') or 'expéditeur inconnu'}.\n\n"
            f"Ouvrez le tableau de bord pour valider : {reference}"
        )
        _send_system_notification(recipient, subject, body)
    except Exception as exc:  # pragma: no cover - defensive, see docstring
        logger.warning("notifications: failed to notify for run %s: %s", run_id, exc)


def notify_overdue_approval(run_id: str, run: dict, overdue_by_seconds: int, due_at: str | None) -> str | None:
    """Notify the escalation target once a pending approval is overdue."""
    if not settings.notify_enabled:
        return None
    recipient = _resolve_escalation_target(run.get("workflow_approver"), run.get("workflow_owner"))
    if not recipient:
        return None
    try:
        subject = "Escalade SLA : validation en retard"
        overdue_for = _format_overdue_duration(overdue_by_seconds)
        due_line = f"Échéance : {due_at}.\n" if due_at else ""
        body = (
            f"La validation du run {run_id} est en retard pour l'email "
            f"« {run.get('subject') or '(sans objet)'} » de "
            f"{run.get('author') or 'expéditeur inconnu'}.\n"
            f"Retard cumulé : {overdue_for}.\n"
            f"{due_line}Veuillez traiter cette validation dans le tableau de bord : {_run_reference(run_id)}"
        )
        if _send_system_notification(recipient, subject, body):
            return recipient
        return None
    except Exception as exc:  # pragma: no cover - defensive, see docstring
        logger.warning("notifications: failed to escalate run %s: %s", run_id, exc)
        return None



def notify_admin_alert(recipient_hint: str | None, subject: str, body: str) -> bool:
    """Send a French, body-safe admin alert through the shared notification channel."""
    recipient = _resolve_admin_recipient(recipient_hint)
    return _send_system_notification(recipient, subject, body)
