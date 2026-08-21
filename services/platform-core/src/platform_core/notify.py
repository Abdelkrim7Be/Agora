from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours} h {minutes} min"
    if hours:
        return f"{hours} h"
    return f"{minutes or 1} min"


class SystemNotifier:
    """Sends platform mail: escalations, alerts, "something needs you" pings.

    Distinct from anything the agent drafts — this never goes through human
    approval, because there is nothing to approve about a notification, and it
    never carries the message body it is about. A failure here is logged and
    swallowed: an undeliverable notice must not fail the work that triggered it.
    """

    def __init__(
        self,
        *,
        enabled: Callable[[], bool],
        send: Callable[..., object],
        app_base_url: Callable[[], str],
        resolve_role: Callable[[str], object | None],
    ) -> None:
        self._enabled = enabled
        self._send = send
        self._app_base_url = app_base_url
        self._resolve_role = resolve_role

    def resolve_recipient(self, *candidates: str | None) -> str | None:
        """First candidate that names a real mailbox wins.

        A literal address is taken as-is; anything else is looked up in the role
        directory. Deliberately returns one recipient, never a list: a
        notification with several owners has none.
        """
        for candidate in candidates:
            cleaned = (candidate or "").strip()
            if not cleaned:
                continue
            if "@" in cleaned:
                return cleaned.lower()
            resolved = self._resolve_role(cleaned)
            primary = getattr(resolved, "primary_email", None)
            if primary:
                return primary
        return None

    def send(self, recipient: str | None, subject: str, body: str) -> bool:
        if not self._enabled() or not recipient:
            return False
        try:
            self._send(to=recipient, subject=subject, body=body)
            return True
        except Exception as exc:  # pragma: no cover - defensive, see class docstring
            logger.warning("notifications: failed to send system mail to %s: %s", recipient, exc)
            return False

    def run_reference(self, run_id: str) -> str:
        """A deep link when the app URL is configured, the bare id otherwise."""
        base = self._app_base_url()
        return f"{base.rstrip('/')}/#run/{run_id}" if base else run_id
