from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from src.config import settings

_USER_SAFE_RE = re.compile(r"[^A-Za-z0-9_.@-]+")
_current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def normalize_user_id(user_id: str | None) -> str:
    value = (user_id or settings.default_user_id).strip()
    value = _USER_SAFE_RE.sub("_", value)
    return value.strip("._-") or settings.default_user_id


def resolve_user_id(external_id: str | None) -> str:
    """Resolve an external identity (e.g. a Gmail address) to the platform user id.

    Consults AGENT_USER_MAP so webhook-driven runs land under the same tenant key
    the gateway propagates via X-Agora-User. Falls back to the normalized external
    id when no mapping is configured (single-tenant collapses to the default).
    """
    mapped = settings.user_map.get((external_id or "").strip().lower())
    return normalize_user_id(mapped or external_id)


def current_user_id() -> str:
    return normalize_user_id(_current_user_id.get())


@contextmanager
def user_context(user_id: str | None) -> Iterator[str]:
    resolved = normalize_user_id(user_id)
    token = _current_user_id.set(resolved)
    try:
        yield resolved
    finally:
        _current_user_id.reset(token)
