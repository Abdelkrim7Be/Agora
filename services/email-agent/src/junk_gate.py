"""Deterministic junk gate: keeps bulk/no-reply mail out of the validation box.

Runs in the poller BEFORE the thread fetch and the LLM triage, on signals that
are already present on the message metadata (Gmail category labels, sender
address, List-Unsubscribe/Precedence headers) — no extra Gmail API calls.

Precedence rule: an email that matches a configured workflow/category is never
junk-gated; the gate only sees mail that no category claimed.
"""

from __future__ import annotations

import re

JUNK_GMAIL_LABELS = {
    "CATEGORY_PROMOTIONS",
    "CATEGORY_SOCIAL",
    "CATEGORY_UPDATES",
    "CATEGORY_FORUMS",
}

_JUNK_SENDER_LOCAL = re.compile(
    r"\b(no-?reply|do-?not-?reply|notifications?|newsletter|news|mailer-daemon|postmaster|marketing|promo(?:tions)?)@",
    re.IGNORECASE,
)

_JUNK_SENDER_DOMAIN = re.compile(
    r"@(?:[a-z0-9-]+\.)*(linkedin|facebookmail|facebook|twitter|x|instagram|tiktok|pinterest|youtube|medium|substack)\.com\b",
    re.IGNORECASE,
)


def is_junk(email_input: dict) -> tuple[bool, str]:
    """Return (junk, reason). Purely deterministic; safe on partial inputs."""
    labels = set(email_input.get("labels") or [])
    category_hits = labels & JUNK_GMAIL_LABELS
    if category_hits:
        return True, f"gmail:{sorted(category_hits)[0].lower()}"
    author = str(email_input.get("author") or "")
    if _JUNK_SENDER_LOCAL.search(author):
        return True, "sender:noreply"
    if _JUNK_SENDER_DOMAIN.search(author):
        return True, "sender:bulk-domain"
    if email_input.get("list_unsubscribe"):
        return True, "header:list-unsubscribe"
    if email_input.get("precedence_bulk"):
        return True, "header:precedence-bulk"
    return False, ""
