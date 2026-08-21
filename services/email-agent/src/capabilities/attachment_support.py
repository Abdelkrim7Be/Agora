"""Shared attachment resolution for send-style tools (write_email, create_draft, ...).

Two independent sources feed a tool's outgoing attachments, both capped by
AGENT_MAX_ATTACHMENT_BYTES / AGENT_MAX_ATTACHMENT_COUNT:

- reattach_originals(): the current message's own attachments, opt-in via the
  tool's include_attachments argument — model-controlled.
- current_uploaded_attachments: files a reviewer staged through
  POST /run/{id}/attachments before approving — trusted context tool_node
  injects, the model never sees or sets this.
"""

from __future__ import annotations

from src.capabilities import current_email_attachments, current_email_id, current_uploaded_attachments
from src.config import settings
from src.mail import get_provider
from src.send_mode import effective_dry_run


def cap_attachments(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Trim a combined attachment list to the configured count/byte cap.

    Each source is already capped on its own; this is the final check on what
    they add up to together.
    """
    kept: list[dict] = []
    notes: list[str] = []
    total_bytes = 0
    for item in items:
        filename = item.get("filename") or "attachment"
        size = len(item.get("data") or b"")
        if len(kept) >= settings.max_attachment_count or total_bytes + size > settings.max_attachment_bytes:
            notes.append(f"Skipped '{filename}': combined attachment limit reached.")
            continue
        total_bytes += size
        kept.append(item)
    return kept, notes


def reattach_originals() -> tuple[list[dict], list[str]]:
    """Download the current message's attachments up to the configured cap.

    Returns (attachments, notes). An attachment that would push the total
    over the cap is skipped rather than failing the send — the note says
    what was dropped.
    """
    available = current_email_attachments.get()
    if not available:
        return [], ["No attachments were found on the original message."]
    if effective_dry_run():
        # download_attachment has no dry-run guard of its own (unlike the
        # send/draft helpers) — calling it here would force a real OAuth
        # resource build on a simulated run. Report what would happen instead.
        names = ", ".join(item.get("filename") or "attachment" for item in available[: settings.max_attachment_count])
        return [], [f"Would attach original file(s) ({names}) — simulated, not downloaded."]
    message_id = current_email_id.get()
    provider = get_provider()
    attachments: list[dict] = []
    notes: list[str] = []
    total_bytes = 0
    for item in available:
        filename = item.get("filename") or "attachment"
        if len(attachments) >= settings.max_attachment_count:
            notes.append(f"Skipped '{filename}': attachment count limit reached.")
            continue
        attachment_id = item.get("attachment_id")
        if not message_id or not attachment_id:
            notes.append(f"Skipped '{filename}': no trusted attachment reference.")
            continue
        size = int(item.get("size") or 0)
        if size and total_bytes + size > settings.max_attachment_bytes:
            notes.append(f"Skipped '{filename}': attachment size limit reached.")
            continue
        try:
            data = provider.download_attachment(message_id, attachment_id)
        except Exception as exc:
            notes.append(f"Skipped '{filename}': could not download ({exc}).")
            continue
        if total_bytes + len(data) > settings.max_attachment_bytes:
            notes.append(f"Skipped '{filename}': attachment size limit reached.")
            continue
        total_bytes += len(data)
        attachments.append(
            {"filename": filename, "mime_type": item.get("mime_type"), "data": data}
        )
    return attachments, notes


def resolve_attachments(include_attachments: bool) -> tuple[list[dict], list[str]]:
    """Combine opt-in reattached originals with any reviewer upload, capped together."""
    notes: list[str] = []
    reattached: list[dict] = []
    if include_attachments:
        reattached, reattach_notes = reattach_originals()
        notes.extend(reattach_notes)
    uploaded = list(current_uploaded_attachments.get())
    attachments, cap_notes = cap_attachments(reattached + uploaded)
    notes.extend(cap_notes)
    return attachments, notes
