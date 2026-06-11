from __future__ import annotations

from typing import Any, List


def parse_email(email_input: dict) -> tuple[str, str, str, str]:
    """Parse an email input dictionary into (author, to, subject, email_thread)."""
    return (
        email_input["author"],
        email_input["to"],
        email_input["subject"],
        email_input["email_thread"],
    )


def format_email_markdown(subject, author, to, email_thread, attachments=None) -> str:
    """Format email details into a readable markdown block."""
    att_section = ""
    if attachments:
        from src.gmail_client import format_attachments
        att_str = format_attachments(attachments)
        if att_str:
            att_section = f"**Attachments**: {att_str}\n\n"
    return f"""

**Subject**: {subject}
**From**: {author}
**To**: {to}

{att_section}{email_thread}

---
"""


def format_draft_markdown(args: dict) -> str:
    """Render a write_email tool-call args as a markdown preview for the approval UI."""
    to = args.get("to", "")
    subject = args.get("subject", "")
    content = args.get("content", "")
    return f"**To**: {to}\n**Subject**: {subject}\n\n{content}"


def extract_tool_call_names(messages: List[Any]) -> List[str]:
    """Collect the names of every tool call across a list of messages."""
    names: List[str] = []
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls and isinstance(message, dict):
            tool_calls = message.get("tool_calls")
        if tool_calls:
            names.extend(call["name"] for call in tool_calls)
    return names
