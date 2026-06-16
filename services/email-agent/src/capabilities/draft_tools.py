from __future__ import annotations

from langchain_core.tools import tool

from src.capabilities import current_gmail_thread_id
from src.gmail_client import create_draft as create_gmail_draft


@tool
def create_draft(to: str, subject: str, content: str) -> str:
    """Create a Gmail draft for the current email thread without sending it."""
    thread_id = current_gmail_thread_id.get()
    result = create_gmail_draft(to=to, subject=subject, body=content, thread_id=thread_id)
    draft_id = result.get("id") if isinstance(result, dict) else None
    if draft_id:
        return f"Created draft '{draft_id}' to {to} with subject '{subject}'."
    return f"Created draft to {to} with subject '{subject}'."


TOOLS = [create_draft]
TOOLS_PROMPT = """
1. create_draft(to, subject, content) - Create a Gmail draft without sending it
"""

# Creating a draft has no external send side effect; policy still authorizes it.
REQUIRES_APPROVAL: set[str] = set()
