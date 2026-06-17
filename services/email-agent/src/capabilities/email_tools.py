from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel

from src.capabilities import current_email_id, hitl_approved
from src.config import settings


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
    if settings.dry_run:
        return f"Email sent to {to} with subject '{subject}' [dry run]"
    _require_approval("write_email")
    from langchain_google_community import GmailToolkit

    from src.gmail_client import gmail_resource

    toolkit = GmailToolkit(api_resource=gmail_resource())
    send_tool = next(t for t in toolkit.get_tools() if t.name == "send_gmail_message")
    return send_tool.invoke({"message": content, "to": [to], "subject": subject})


@tool
def forward_email(to: str, note: str = "") -> str:
    """Forward the current email to a recipient."""
    message_id = _message_id()
    if settings.dry_run:
        return f"Forwarded current email to {to} [dry run]"
    _require_approval("forward_email")

    from src.gmail_client import forward_message

    result = forward_message(message_id, to=to, note=note)
    sent_id = result.get("id") if isinstance(result, dict) else None
    return f"Forwarded current email to {to}" + (
        f" (message id: {sent_id})" if sent_id else ""
    )


@tool
def reply_all(content: str) -> str:
    """Reply to all participants on the current email thread."""
    message_id = _message_id()
    if settings.dry_run:
        return "Reply-all sent on the current thread [dry run]"
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


TOOLS = [write_email, forward_email, reply_all, Done]
TOOLS_PROMPT = """
1. write_email(to, subject, content) - Send emails to specified recipients
2. forward_email(to, note) - Forward the current email to a recipient
3. reply_all(content) - Reply to all participants on the current email thread
4. Done - E-mail has been sent
"""

# Tools that require human approval before executing (HITL gate).
REQUIRES_APPROVAL = {"write_email", "forward_email", "reply_all"}
