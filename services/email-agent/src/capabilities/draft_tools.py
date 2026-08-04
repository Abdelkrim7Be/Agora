from __future__ import annotations

from langchain_core.tools import tool

from src.capabilities import UntrustedRecipientError, current_gmail_thread_id, current_reply_to
from src.mail import get_provider


@tool
def create_draft(subject: str, content: str) -> str:
    """Create a Gmail draft replying to the sender of the current email."""
    to = current_reply_to.get()
    if not to:
        raise UntrustedRecipientError(
            "No trusted reply address is available for this message, so no draft was created."
        )
    thread_id = current_gmail_thread_id.get()
    result = get_provider().create_draft(to=to, subject=subject, body=content, thread_id=thread_id)
    draft_id = result.get("id") if isinstance(result, dict) else None
    if draft_id:
        return f"Created draft '{draft_id}' to {to} with subject '{subject}'."
    return f"Created draft to {to} with subject '{subject}'."


TOOLS = [create_draft]
TOOLS_PROMPT = """
1. create_draft(subject, content) - Create a Gmail draft replying to the sender
"""

# Creating a draft has no external send side effect; policy still authorizes it.
REQUIRES_APPROVAL: set[str] = set()
