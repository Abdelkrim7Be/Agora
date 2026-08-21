from __future__ import annotations

import logging

from platform_core.notify import SystemNotifier, format_duration

from src.config import settings
from src.mail import get_provider
from src.roles import resolve_role

logger = logging.getLogger(__name__)

_ACTION_LABELS = {
    "write_email": "Brouillon de réponse",
    "reply_all": "Brouillon de réponse (tous)",
    "forward_email": "Transfert",
    "create_draft": "Brouillon",
    "trash_email": "Suppression",
}

_notifier = SystemNotifier(
    enabled=lambda: settings.notify_enabled,
    send=lambda **kwargs: get_provider().send_message(**kwargs),
    app_base_url=lambda: settings.notify_app_base_url,
    resolve_role=resolve_role,
)


def _action_label(action_name: str | None, workflow_owner: str | None) -> str:
    label = _ACTION_LABELS.get(action_name or "", "Action")
    return f"{label} vers {workflow_owner}" if workflow_owner else label


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

    Never includes the email body — it links back to the approval instead. A
    send failure must never break approval creation, hence the broad except.
    """
    if not settings.notify_enabled:
        return
    try:
        recipient = _notifier.resolve_recipient(
            result.get("workflow_approver"), *(result.get("workflow_route_to") or [])
        )
        if not recipient:
            return
        label = _action_label(_pending_action_name(result), result.get("workflow_owner"))
        body = (
            f"Une action est en attente de validation : {label} pour l'email "
            f"« {email_input.get('subject') or '(sans objet)'} » de "
            f"{email_input.get('author') or 'expéditeur inconnu'}.\n\n"
            f"Ouvrez le tableau de bord pour valider : {_notifier.run_reference(run_id)}"
        )
        _notifier.send(recipient, f"Action requise : {label}", body)
    except Exception as exc:  # pragma: no cover - defensive, see docstring
        logger.warning("notifications: failed to notify for run %s: %s", run_id, exc)


def notify_overdue_approval(
    run_id: str, run: dict, overdue_by_seconds: int, due_at: str | None
) -> str | None:
    """Notify the escalation target once a pending approval is overdue."""
    if not settings.notify_enabled:
        return None
    recipient = _notifier.resolve_recipient(run.get("workflow_approver"), run.get("workflow_owner"))
    if not recipient:
        return None
    try:
        due_line = f"Échéance : {due_at}.\n" if due_at else ""
        body = (
            f"La validation du run {run_id} est en retard pour l'email "
            f"« {run.get('subject') or '(sans objet)'} » de "
            f"{run.get('author') or 'expéditeur inconnu'}.\n"
            f"Retard cumulé : {format_duration(overdue_by_seconds)}.\n"
            f"{due_line}Veuillez traiter cette validation dans le tableau de bord : "
            f"{_notifier.run_reference(run_id)}"
        )
        sent = _notifier.send(recipient, "Escalade SLA : validation en retard", body)
        return recipient if sent else None
    except Exception as exc:  # pragma: no cover - defensive, see docstring
        logger.warning("notifications: failed to escalate run %s: %s", run_id, exc)
        return None


def notify_admin_alert(recipient_hint: str | None, subject: str, body: str) -> bool:
    """Send a French, body-safe admin alert through the shared notification channel."""
    return _notifier.send(_notifier.resolve_recipient(recipient_hint), subject, body)
