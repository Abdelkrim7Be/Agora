"""Deterministic junk gate: keeps bulk/no-reply mail out of the validation box.

Runs in the poller BEFORE the thread fetch and the LLM triage, on signals that
are already present on the message metadata (Gmail category labels, sender
address, List-Unsubscribe/List-Id/Precedence/Auto-Submitted headers) — no extra
Gmail API calls.

Precedence rule: an email that matches a configured workflow/category is never
junk-gated; the gate only sees mail that no category claimed.

Within the gate, the per-instance `JunkConfig` allowlist wins over everything,
then its blocklist, then the built-in heuristics (each family switchable).
"""

from __future__ import annotations

import re

from src.junk_config import (
    JunkConfig,
    address_domain,
    address_matches,
    domain_matches,
    normalize_address,
)

JUNK_GMAIL_LABELS = {
    "CATEGORY_PROMOTIONS",
    "CATEGORY_SOCIAL",
    "CATEGORY_UPDATES",
    "CATEGORY_FORUMS",
}

# Local parts that only ever originate machine or campaign mail. Deliberately
# excludes ambiguous business addresses (info@, contact@, support@, hello@) —
# those are frequently the human side of a real conversation.
_JUNK_SENDER_LOCAL = re.compile(
    r"\b(no-?reply|do-?not-?reply|not?[-.]?reply|notifications?|newsletters?|news|"
    r"mailer-daemon|postmaster|mailer|mailing|marketing|promo(?:tions?)?|offers?|"
    r"deals?|campaign|bounce[sd]?|unsubscribe|auto-?confirm|noresponse)@",
    re.IGNORECASE,
)

_JUNK_SENDER_DOMAIN = re.compile(
    r"@(?:[a-z0-9-]+\.)*(linkedin|facebookmail|facebook|twitter|x|instagram|tiktok|pinterest|youtube|medium|substack|quora|reddit)\.com\b",
    re.IGNORECASE,
)

# Subdomains email service providers use for campaign traffic. Requiring a
# subdomain keeps ordinary person@company.com senders untouched while catching
# bershka@news.bershka.com or service@mail.temu.com.
_BULK_SUBDOMAINS = {
    "news", "newsletter", "newsletters", "mail", "mails", "email", "emails",
    "em", "e", "mailer", "mailing", "send", "sending", "sender", "smtp",
    "marketing", "mkt", "campaign", "campaigns", "promo", "promotions",
    "notification", "notifications", "notify", "alerts", "alert", "updates",
    "update", "info", "reply", "no-reply", "noreply", "link", "links", "click",
    "clicks", "crm", "engage", "contact",
    # Marketing-only prefixes seen on real bulk mail that reached the model and
    # was ignored anyway (hello.bitdefender.com, engage.canva.com and friends).
    # "orders", "billing" and "invoice" are deliberately absent: those carry
    # transactional mail a business mailbox has to see.
    "hello", "digest", "social", "community", "connect", "shop", "store",
    "deals", "offers", "message", "messages", "inbox",
}

# Agora AI's own component-health alert mail (alerts.py). The admin recipient is
# frequently the same mailbox the agent monitors — in that case the alert
# lands right back in the inbox it was sent from and gets triaged like any
# other message. Not a policy choice: this is always noise, so it is checked
# unconditionally, ahead of the switchable heuristics below.
_SYSTEM_ALERT_SUBJECT_PREFIXES = ("Alerte Agora AI :", "Resolution Agora AI :")

# Bulk-sending platforms: mail from these is campaign traffic whatever the
# local part looks like.
_ESP_DOMAINS = {
    "sendgrid.net", "mailgun.org", "mailgun.net", "mcsv.net", "mcdlv.net",
    "rsgsv.net", "mailchimpapp.net", "mandrillapp.com", "sparkpostmail.com",
    "amazonses.com", "sendinblue.com", "brevo.com", "klaviyomail.com",
    "hubspotemail.net", "exacttarget.com", "mktdns.com", "createsend.com",
    "cmail19.com", "cmail20.com", "sailthru.com", "salesforce-email.com",
    "intercom-mail.com", "customeriomail.com", "postmarkapp.com",
    # Newsletter platforms: everything they send is a subscription broadcast.
    "substack.com", "beehiiv.com", "mailerlite.com", "buttondown.email",
    "ghost.io", "revue.email", "sendfox.com", "aweber.com", "getresponse.com",
    "activehosted.com", "omnisend.com", "sendpulse.com",
    # Retail campaign domains that exist only to send bulk.
    "temuemail.com", "eu.temuemail.com",
}


def _has_bulk_subdomain(domain: str) -> bool:
    parts = domain.split(".")
    # Needs at least sub.domain.tld to have a subdomain to inspect.
    return len(parts) >= 3 and parts[0] in _BULK_SUBDOMAINS


def is_junk(email_input: dict, config: JunkConfig | None = None) -> tuple[bool, str]:
    """Return (junk, reason). Purely deterministic; safe on partial inputs."""
    config = config or JunkConfig()
    if not config.enabled:
        return False, ""

    author = str(email_input.get("author") or "")
    address = normalize_address(author)
    domain = address_domain(author)

    subject = str(email_input.get("subject") or "")
    if subject.startswith(_SYSTEM_ALERT_SUBJECT_PREFIXES):
        return True, "system:self-alert"

    if address_matches(address, config.allowed_senders) or domain_matches(domain, config.allowed_domains):
        return False, ""
    if address_matches(address, config.blocked_senders):
        return True, "config:blocked-sender"
    if domain_matches(domain, config.blocked_domains):
        return True, "config:blocked-domain"

    if config.gmail_categories:
        labels = set(email_input.get("labels") or [])
        category_hits = labels & JUNK_GMAIL_LABELS
        if category_hits:
            return True, f"gmail:{sorted(category_hits)[0].lower()}"

    if config.sender_heuristics:
        if _JUNK_SENDER_LOCAL.search(author):
            return True, "sender:noreply"
        if _JUNK_SENDER_DOMAIN.search(author):
            return True, "sender:bulk-domain"
        if domain_matches(domain, sorted(_ESP_DOMAINS)):
            return True, "sender:esp-domain"
        if _has_bulk_subdomain(domain):
            return True, "sender:bulk-subdomain"

    if config.bulk_headers:
        if email_input.get("list_unsubscribe"):
            return True, "header:list-unsubscribe"
        if email_input.get("precedence_bulk"):
            return True, "header:precedence-bulk"
        if email_input.get("list_id"):
            return True, "header:list-id"
        if email_input.get("auto_submitted"):
            return True, "header:auto-submitted"

    return False, ""
