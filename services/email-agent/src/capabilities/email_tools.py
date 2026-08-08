from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel

from src.capabilities import (
    UntrustedRecipientError,
    current_email_id,
    current_reply_to,
    current_route_targets,
    hitl_approved,
)
from src.capabilities.attachment_support import resolve_attachments
from src.config import settings  # noqa: F401 — tests patch dry_run through this module
from src.mail import get_provider
from src.send_mode import SIMULATED_NOTE, effective_dry_run


def _message_id() -> str:
    message_id = current_email_id.get()
    if not message_id:
        raise RuntimeError(
            "Email thread send tools require a trusted email_id from the graph context."
        )
    return message_id


def _reply_recipient() -> str:
    """The address the current message came from.

    Read from the message headers by the graph, never from the model. A tool
    that can only reply to its own sender cannot be redirected by an injected
    instruction, whatever the mail body says.
    """
    address = current_reply_to.get()
    if not address:
        raise UntrustedRecipientError(
            "No trusted reply address is available for this message, so nothing was sent."
        )
    return address


def _routed_recipients() -> list[str]:
    """Destinations the workspace configured for this workflow.

    Sourced from the workflow's route_to / owner and resolved through the roles
    directory before the model ever runs.
    """
    targets = [t for t in current_route_targets.get() if t]
    if not targets:
        raise UntrustedRecipientError(
            "This workflow has no internal recipient configured, so no notification was sent."
        )
    return targets


def _require_approval(tool_name: str) -> None:
    if not hitl_approved.get():
        raise RuntimeError(
            f"{tool_name} requires human approval — call via the graph API, not directly."
        )


@tool
def write_email(subject: str, content: str, include_attachments: bool = False) -> str:
    """Reply to the sender of the email currently being handled.

    Set include_attachments=True to carry the original message's attachments
    onto the reply (e.g. returning the same file the sender attached).
    """
    to = _reply_recipient()
    if effective_dry_run():
        return f"Email sent to {to} with subject '{subject}' ({SIMULATED_NOTE})"
    _require_approval("write_email")
    attachments, notes = resolve_attachments(include_attachments)
    kwargs = {"to": to, "subject": subject, "body": content}
    if attachments:
        kwargs["attachments"] = attachments
    result = get_provider().send_message(**kwargs)
    sent_id = result.get("id") if isinstance(result, dict) else None
    summary = f"Email sent to {to} with subject '{subject}'" + (
        f" (message id: {sent_id})" if sent_id else ""
    )
    if attachments:
        summary += f" Attached {len(attachments)} file(s)."
    if notes:
        summary += " " + " ".join(notes)
    return summary


@tool
def forward_email(note: str = "") -> str:
    """Forward the current email to this workflow's configured internal recipients."""
    message_id = _message_id()
    recipients = _routed_recipients()

    if effective_dry_run():
        return f"Forwarded current email to {', '.join(recipients)} ({SIMULATED_NOTE})"
    _require_approval("forward_email")

    provider = get_provider()
    sent_ids = []
    for recipient in recipients:
        result = provider.forward_message(message_id, to=recipient, note=note)
        sent_id = result.get("id") if isinstance(result, dict) else None
        if sent_id:
            sent_ids.append(sent_id)

    return f"Forwarded current email to {', '.join(recipients)}" + (
        f" (message ids: {', '.join(sent_ids)})" if sent_ids else ""
    )


@tool
def notify_internal(subject: str, note: str) -> str:
    """Send an internal workflow notification (not a forward of the original email)."""
    recipients = _routed_recipients()

    if effective_dry_run():
        return f"Notified {', '.join(recipients)} ({SIMULATED_NOTE})"
    _require_approval("notify_internal")

    result = get_provider().notify_internal_message(to=recipients, subject=subject, note=note)
    sent_id = result.get("id") if isinstance(result, dict) else None
    return f"Notified {', '.join(recipients)}" + (
        f" (message id: {sent_id})" if sent_id else ""
    )


@tool
def reply_all(content: str) -> str:
    """Reply to all participants on the current email thread."""
    message_id = _message_id()
    if effective_dry_run():
        return f"Reply-all sent on the current thread ({SIMULATED_NOTE})"
    _require_approval("reply_all")

    result = get_provider().reply_all_message(message_id, body=content)
    sent_id = result.get("id") if isinstance(result, dict) else None
    return "Reply-all sent on the current thread" + (
        f" (message id: {sent_id})" if sent_id else ""
    )


@tool
class Done(BaseModel):
    """E-mail has been sent."""

    # Accept bool OR str with a default: Groq's llama intermittently emits the
    # stringified "true" instead of a boolean, and Groq rejects the tool call before
    # we ever see it. Routing only checks the tool name, so the value is irrelevant —
    # a permissive, optional schema just avoids the 400 tool-validation error.
    done: bool | str = True


TOOLS = [write_email, forward_email, notify_internal, reply_all, Done]
TOOLS_PROMPT = """
1. write_email(subject, content, include_attachments) - Reply to the sender of the current email.
   Set include_attachments=true to carry the original message's attachments onto the reply.
2. forward_email(note) - Forward the current email to this workflow's configured recipients
3. notify_internal(subject, note) - Notify this workflow's configured recipients, not a forward
4. reply_all(content) - Reply to all participants on the current email thread
5. Done - E-mail has been sent
"""

# Tools that require human approval before executing (HITL gate).
REQUIRES_APPROVAL = {"write_email", "forward_email", "notify_internal", "reply_all"}
