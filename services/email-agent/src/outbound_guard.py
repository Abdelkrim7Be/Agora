"""Last-resort recipient allowlist, enforced below every mail provider.

This sits under the security service, under dry-run, and under any model
decision: every real send funnels through it, so an address outside
`AGENT_OUTBOUND_ALLOWLIST` cannot leave the system even if policy is
misconfigured, security is disabled, or the model invents a recipient.

It moved out of `src/gmail_client.py` unchanged when Outlook became a second
provider — the guard has to bind on every provider, not just Gmail.
`gmail_client` re-exports both names, so existing imports keep working.
"""

from __future__ import annotations

from email.utils import getaddresses

from src.config import settings


class OutboundRecipientBlocked(RuntimeError):
    """Raised when a send targets an address outside the outbound allowlist."""


def email_addresses(*values: str) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for _name, address in getaddresses([v for v in values if v]):
        address = address.strip()
        key = address.lower()
        if address and key not in seen:
            seen.add(key)
            results.append(address)
    return results


def enforce_outbound_allowlist(to: str | list[str]) -> None:
    """Refuse to send to anyone outside AGENT_OUTBOUND_ALLOWLIST, when it is set.

    Empty setting (the default) disables the check entirely and leaves normal
    operation untouched. A send with no parseable recipient is blocked too —
    an empty recipient list must never read as "nothing to check".
    """
    allowlist = settings.outbound_allowlist
    if not allowlist:
        return
    recipients = email_addresses(*(to if isinstance(to, list) else [to]))
    blocked = [addr for addr in recipients if addr.lower() not in allowlist]
    if blocked or not recipients:
        raise OutboundRecipientBlocked(
            f"outbound allowlist blocked recipients {blocked or list(to)}; "
            f"allowed: {sorted(allowlist)}"
        )
