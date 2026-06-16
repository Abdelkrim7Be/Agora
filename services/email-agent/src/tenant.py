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
