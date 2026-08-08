from __future__ import annotations

from langchain_core.tools import tool

from src.capabilities import UntrustedRecipientError, current_gmail_thread_id, current_reply_to
from src.capabilities.attachment_support import resolve_attachments
from src.mail import get_provider


@tool
def create_draft(subject: str, content: str, include_attachments: bool = False) -> str:
    """Create a Gmail draft replying to the sender of the current email.

    Set include_attachments=True to carry the original message's attachments
    onto the draft (e.g. returning the same file the sender attached).
    """
    to = current_reply_to.get()
    if not to:
        raise UntrustedRecipientError(
            "No trusted reply address is available for this message, so no draft was created."
        )
    thread_id = current_gmail_thread_id.get()
    attachments, notes = resolve_attachments(include_attachments)
    kwargs = {"to": to, "subject": subject, "body": content, "thread_id": thread_id}
    if attachments:
        kwargs["attachments"] = attachments
    result = get_provider().create_draft(**kwargs)
    draft_id = result.get("id") if isinstance(result, dict) else None
    summary = (
        f"Created draft '{draft_id}' to {to} with subject '{subject}'."
        if draft_id
        else f"Created draft to {to} with subject '{subject}'."
    )
    if attachments:
        summary += f" Attached {len(attachments)} file(s)."
    if notes:
        summary += " " + " ".join(notes)
    return summary


TOOLS = [create_draft]
TOOLS_PROMPT = """
1. create_draft(subject, content, include_attachments) - Create a Gmail draft replying to the
   sender. Set include_attachments=true to carry the original message's attachments onto the draft.
"""

# Creating a draft has no external send side effect; policy still authorizes it.
REQUIRES_APPROVAL: set[str] = set()
