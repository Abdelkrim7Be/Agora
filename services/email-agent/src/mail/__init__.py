"""Mail provider resolution.

`get_provider()` is the one entry point call sites use. It reads the current
instance's configured provider and returns an object satisfying `MailProvider`.

Deliberately **not** cached. Construction does no I/O — the authenticated
connection is built lazily on first use — so a fresh provider per call costs
nothing, and a cache keyed on instance would happily hand back a provider
holding a token that was revoked and reconnected in between. Call sites that
want one connection for a whole poll cycle hold the provider for that cycle,
which is exactly how they used to hold the Gmail resource.
"""

from __future__ import annotations

from src.mail.base import MailProvider
from src.mail.setting import (
    DEFAULT_PROVIDER,
    MAIL_PROVIDERS,
    get_mail_provider,
    set_mail_provider,
)

__all__ = [
    "MailProvider",
    "MAIL_PROVIDERS",
    "DEFAULT_PROVIDER",
    "get_mail_provider",
    "set_mail_provider",
    "get_provider",
]


def get_provider(
    user_id: str | None = None,
    agent_instance_id: str | None = None,
    resource=None,
) -> MailProvider:
    """Build the mail provider for an agent instance.

    `resource` injects an already-built connection — used by the poller, which
    builds one per cycle, and by tests, which pass fakes.
    """
    provider = get_mail_provider(agent_instance_id)
    if provider == "outlook":
        from src.mail.outlook import OutlookProvider

        return OutlookProvider(user_id=user_id, agent_instance_id=agent_instance_id, session=resource)

    from src.mail.gmail import GmailProvider

    return GmailProvider(user_id=user_id, resource=resource)
