from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel

from src.capabilities import current_email_id, hitl_approved
from src.config import settings  # noqa: F401 — tests patch dry_run through this module
from src.send_mode import SIMULATED_NOTE, effective_dry_run


def _message_id() -> str:
    message_id = current_email_id.get()
    if not message_id:
        raise RuntimeError(
            "Email thread send tools require a trusted email_id from the graph context."
        )
    return message_id


def _require_approval(tool_name: str) -> None:
    if not hitl_approved.get():
        raise RuntimeError(
            f"{tool_name} requires human approval — call via the graph API, not directly."
        )


@tool
def write_email(to: str, subject: str, content: str) -> str:
    """Write and send an email."""
    if effective_dry_run():
        return f"Email sent to {to} with subject '{subject}' ({SIMULATED_NOTE})"
    _require_approval("write_email")
    from src.gmail_client import send_message

    result = send_message(to=to, subject=subject, body=content)
    sent_id = result.get("id") if isinstance(result, dict) else None
    return f"Email sent to {to} with subject '{subject}'" + (
        f" (message id: {sent_id})" if sent_id else ""
    )


@tool
def forward_email(to: str | list[str], note: str = "") -> str:
    """Forward the current email to a recipient or list of recipients."""
    message_id = _message_id()
    recipients = [to] if isinstance(to, str) else to
    recipients = [r for r in recipients if r]

    if not recipients:
        return "No recipients provided to forward to."

    if effective_dry_run():
        return f"Forwarded current email to {', '.join(recipients)} ({SIMULATED_NOTE})"
    _require_approval("forward_email")

    from src.gmail_client import forward_message

    sent_ids = []
    for recipient in recipients:
        result = forward_message(message_id, to=recipient, note=note)
        sent_id = result.get("id") if isinstance(result, dict) else None
        if sent_id:
            sent_ids.append(sent_id)

    return f"Forwarded current email to {', '.join(recipients)}" + (
        f" (message ids: {', '.join(sent_ids)})" if sent_ids else ""
    )


@tool
def notify_internal(to: str | list[str], subject: str, note: str) -> str:
    """Send an internal workflow notification (not a forward of the original email)."""
    recipients = [to] if isinstance(to, str) else to
    recipients = [r for r in recipients if r]

    if not recipients:
        return "No recipients provided to notify."

    if effective_dry_run():
        return f"Notified {', '.join(recipients)} ({SIMULATED_NOTE})"
    _require_approval("notify_internal")

    from src.gmail_client import notify_internal_message

    result = notify_internal_message(to=recipients, subject=subject, note=note)
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

    from src.gmail_client import reply_all_message

    result = reply_all_message(message_id, body=content)
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
1. write_email(to, subject, content) - Send emails to specified recipients
2. forward_email(to, note) - Forward the current email to a recipient or list of recipients
3. notify_internal(to, subject, note) - Send an internal workflow notification, not a forward
4. reply_all(content) - Reply to all participants on the current email thread
5. Done - E-mail has been sent
"""

# Tools that require human approval before executing (HITL gate).
REQUIRES_APPROVAL = {"write_email", "forward_email", "notify_internal", "reply_all"}
