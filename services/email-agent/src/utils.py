from __future__ import annotations

import re
from typing import Any, List

from src.config import settings

# Where a mail client stops writing the reply and starts repeating the message
# being replied to. Anchored at line start so a sentence that merely contains
# one of these words is not mistaken for a divider.
_QUOTE_MARKERS = (
    # Outlook / Gmail attribution lines, English and French. The address part may
    # wrap across lines, so the gap is matched loosely but bounded.
    r"^On\s.{0,400}?\swrote:\s*$",
    r"^Le\s.{0,400}?\sa\s+écrit\s*:\s*$",
    r"^-{2,}\s*(Original Message|Forwarded message|Message d'origine|Message transféré)\s*-{2,}\s*$",
    # Outlook's horizontal rule above the quoted block.
    r"^_{10,}\s*$",
    # Header block a client re-emits above the quote.
    r"^From:\s.+\nSent:\s",
    r"^De\s*:\s.+\nEnvoyé\s*:\s",
    # A run of quoted lines. One is not enough — a single ">" appears in prose.
    r"^>.*\n(?:>.*\n){2,}",
)

_QUOTE_PATTERN = re.compile("|".join(_QUOTE_MARKERS), re.MULTILINE | re.IGNORECASE)

# Below this, the text before the divider is too short to be the real reply —
# more likely the marker matched something inside the first line and cutting
# there would throw away the message.
_MIN_KEPT_CHARS = 40

# What `format_thread` puts between two messages of the same conversation. Shared
# so the prompt-time cleanup can tell one message's quoted tail from the next
# real message and never cut the thread short.
THREAD_BLOCK_SEPARATOR = "\n\n---\n\n"


def strip_quoted_reply(body: str) -> str:
    """Drop the quoted history a mail client appends below a reply.

    In a thread of N messages, every message carries a copy of the ones before
    it, so the same text is paid for N times in one prompt — the single largest
    avoidable input cost on the hot path. The model gains nothing: the thread is
    already assembled chronologically from the individual messages.

    Conservative by construction. If the text above the divider is too short to
    be a real reply, the body is returned untouched: sending a redundant quote to
    the model is cheap, losing the actual message is not.
    """
    if not body:
        return body
    match = _QUOTE_PATTERN.search(body)
    if not match:
        return body
    kept = body[: match.start()].rstrip()
    if len(kept.strip()) < _MIN_KEPT_CHARS:
        return body
    return kept


def clamp_email_body(email_thread: str) -> str:
    """Cap the body that goes into a prompt.

    `format_thread` bounds a Gmail-fetched *thread* (N messages x per-message
    truncation), but three paths reach a model without passing through it: a
    single message with no thread, the manual `/run` API where the body is a free
    string from the client, and the poller appending extracted attachment text —
    capped per PDF, uncapped in aggregate.

    So the body was the one unbounded input on the hot path, and it is paid twice
    per email: once at triage, once at drafting. Keeping the head is deliberate —
    the ask in a business email is at the top, and quoted history at the bottom is
    what a long body is usually made of.

    This only shapes the prompt. The run record and the UI keep the full text;
    truncating at ingestion would lose it — which is also why the quoted-history
    cleanup runs here and not in the provider's parser.
    """
    email_thread = THREAD_BLOCK_SEPARATOR.join(
        strip_quoted_reply(block) for block in email_thread.split(THREAD_BLOCK_SEPARATOR)
    )
    limit = settings.email_body_max_chars
    if limit <= 0 or len(email_thread) <= limit:
        return email_thread
    return email_thread[:limit].rstrip() + "\n\n…[message tronqué]"


def parse_email(email_input: dict) -> tuple[str, str, str, str]:
    """Parse an email input dictionary into (author, to, subject, email_thread).

    Every caller of this builds a prompt, which is why the body cap lives here.
    """
    return (
        email_input["author"],
        email_input["to"],
        email_input["subject"],
        clamp_email_body(email_input["email_thread"]),
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


def _recipients_line(args: dict) -> str:
    """Recipients for the approval preview.

    Send tools no longer take a `to` argument — tool_node passes the trusted
    recipients under `_recipients` so the approver still sees the destination.
    `to` is still read for older stored runs and for direct callers.
    """
    value = args.get("_recipients") or args.get("to") or ""
    return ", ".join(value) if isinstance(value, (list, tuple)) else str(value)


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
        note = args.get("note", "")
        return f"**Forward to**: {_recipients_line(args)}\n\n{note}"
    if name == "notify_internal":
        subject = args.get("subject", "")
        note = args.get("note", "")
        return f"**Internal notification to**: {_recipients_line(args)}\n**Subject**: {subject}\n\n{note}"
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
