from __future__ import annotations

from src.sensitivity_config import (
    SensitivityConfig,
    address_domain,
    address_matches,
    domain_matches,
    normalize_address,
)


def is_sensitive(headers: dict, config: SensitivityConfig | None = None) -> tuple[bool, str]:
    """Return (sensitive, reason) using only sender/domain/subject metadata."""
    config = config or SensitivityConfig()
    if not config.enabled or not config.has_rules():
        return False, ""

    author = str(headers.get("author") or headers.get("from") or "")
    subject = str(headers.get("subject") or "")
    address = normalize_address(author)
    domain = address_domain(author)

    if address_matches(address, config.allowed_senders) or domain_matches(domain, config.allowed_domains):
        return False, ""
    if address_matches(address, config.blocked_senders):
        return True, "sender:blocked"
    if domain_matches(domain, config.blocked_domains):
        return True, "domain:blocked"

    subject_lower = subject.lower()
    for keyword in config.subject_keywords:
        cleaned = (keyword or "").strip().lower()
        if cleaned and cleaned in subject_lower:
            return True, "subject:keyword"

    return False, ""
