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


def format_action_description(name: str, args: dict) -> str:
    """Render a human-readable approval preview for any HITL-gated tool call.

    Kept in the same 'description' markdown field the HumanInterrupt schema
    already exposes (Agent Inbox reads it directly) so every gated action gets a
    real preview instead of a generic "Approve 'tool_name'?" string.
    """
    if name == "write_email":
        return "**Reply draft**\n\n" + format_draft_markdown(args)
    if name == "create_draft":
        return "**Draft (not sent)**\n\n" + format_draft_markdown(args)
    if name == "reply_all":
        return f"**Reply-all draft**\n\n{args.get('content', '')}"
    if name == "forward_email":
        to = args.get("to", "")
        targets = ", ".join(to) if isinstance(to, list) else str(to)
        note = args.get("note", "")
        return f"**Forward to**: {targets}\n\n{note}"
    if name == "trash_email":
        return "**Move this email to trash?**"
    return f"Approve '{name}'?"


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


_CLOSING_PHRASES = (
    "cordialement",
    "bien à vous",
    "bien cordialement",
    "sincères salutations",
    "meilleures salutations",
    "best regards",
    "kind regards",
    "regards",
)

_GREETING_PREFIXES = ("bonjour", "bonsoir", "cher ", "chère ", "hello", "hi ", "dear ")


def ensure_email_paragraphs(content: str, min_length: int = 400) -> str:
    """Safety net for degenerate single-paragraph drafts.

    Small local models sometimes emit one dense block even when asked for
    structure. When a long draft contains no blank line, split it into
    salutation / short paragraphs / closing so the HTML renderer can produce
    real <p> blocks. Structured content is returned untouched.
    """
    text = (content or "").strip()
    if not text or len(text) < min_length or "\n\n" in text:
        return content

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    flat = " ".join(lines)

    greeting = ""
    lowered = flat.lower()
    for prefix in _GREETING_PREFIXES:
        if lowered.startswith(prefix):
            cut = flat.find(",")
            if 0 < cut < 60:
                greeting = flat[: cut + 1]
                flat = flat[cut + 1 :].strip()
            break

    closing = ""
    lowered = flat.lower()
    for phrase in _CLOSING_PHRASES:
        idx = lowered.rfind(phrase)
        if idx != -1 and len(flat) - idx < 80:
            closing = flat[idx:].strip()
            flat = flat[:idx].rstrip(" ,.;")
            if flat:
                flat += "."
            break

    import re as _re

    sentences = [s.strip() for s in _re.split(r"(?<=[.!?])\s+", flat) if s.strip()]
    paragraphs = []
    for i in range(0, len(sentences), 2):
        paragraphs.append(" ".join(sentences[i : i + 2]))

    blocks = [b for b in [greeting, *paragraphs, closing] if b]
    return "\n\n".join(blocks)
