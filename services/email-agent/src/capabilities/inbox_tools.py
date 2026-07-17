from __future__ import annotations

from langchain_core.tools import tool

from src.capabilities import current_email_id
from src.config import settings  # noqa: F401 — tests patch dry_run through this module
from src.send_mode import effective_dry_run
from src.gmail_client import (
    archive_message,
    ensure_label,
    list_labels,
    modify_labels,
    trash_message,
)


def _message_id() -> str:
    message_id = current_email_id.get()
    if not message_id:
        raise RuntimeError("Inbox tools require a trusted email_id from the graph context.")
    return message_id


def _label_id(label: str) -> str:
    if effective_dry_run():
        return f"dry-run-label:{label}"
    for existing in list_labels():
        if label in (existing.get("id"), existing.get("name")):
            return existing["id"]
    raise ValueError(f"Gmail label not found: {label}")


@tool
def apply_label(label: str) -> str:
    """Apply a Gmail label to the current email."""
    message_id = _message_id()
    label_id = ensure_label(label)
    modify_labels(message_id, add_label_ids=[label_id])
    return f"Applied label '{label}' to the current email."


@tool
def remove_label(label: str) -> str:
    """Remove a Gmail label from the current email."""
    message_id = _message_id()
    label_id = _label_id(label)
    modify_labels(message_id, remove_label_ids=[label_id])
    return f"Removed label '{label}' from the current email."


@tool
def archive_email() -> str:
    """Archive the current email."""
    archive_message(_message_id())
    return "Archived the current email."


@tool
def mark_read() -> str:
    """Mark the current email as read."""
    modify_labels(_message_id(), remove_label_ids=["UNREAD"])
    return "Marked the current email as read."


@tool
def mark_unread() -> str:
    """Mark the current email as unread."""
    modify_labels(_message_id(), add_label_ids=["UNREAD"])
    return "Marked the current email as unread."


@tool
def trash_email() -> str:
    """Move the current email to trash."""
    trash_message(_message_id())
    return "Moved the current email to trash."


TOOLS = [apply_label, remove_label, archive_email, mark_read, mark_unread, trash_email]
TOOLS_PROMPT = """
1. apply_label(label) - Apply a Gmail label to the current email
2. remove_label(label) - Remove a Gmail label from the current email
3. archive_email() - Archive the current email by removing it from the inbox
4. mark_read() - Mark the current email as read
5. mark_unread() - Mark the current email as unread
6. trash_email() - Move the current email to trash
"""

# Trash is reversible for about 30 days, but still destructive enough to require approval.
REQUIRES_APPROVAL = {"trash_email"}
