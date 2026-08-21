from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator, Mapping

_USER_SAFE_RE = re.compile(r"[^A-Za-z0-9_.@-]+")


class TenantScope:
    def __init__(
        self,
        *,
        default_user_id: Callable[[], str],
        default_agent_instance_id: Callable[[], str],
        user_map: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self._default_user_id = default_user_id
        self._default_agent_instance_id = default_agent_instance_id
        self._user_map = user_map or (lambda: {})
        self._current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)
        self._current_agent_instance_id: ContextVar[str | None] = ContextVar(
            "current_agent_instance_id",
            default=None,
        )

    def normalize_user_id(self, user_id: str | None) -> str:
        return _normalize(user_id, self._default_user_id())

    def normalize_agent_instance_id(self, agent_instance_id: str | None) -> str:
        return _normalize(agent_instance_id, self._default_agent_instance_id())

    def resolve_user_id(self, external_id: str | None) -> str:
        mapped = self._user_map().get((external_id or "").strip().lower())
        return self.normalize_user_id(mapped or external_id)

    def current_user_id(self) -> str:
        return self.normalize_user_id(self._current_user_id.get())

    def current_agent_instance_id(self) -> str:
        return self.normalize_agent_instance_id(self._current_agent_instance_id.get())

    @contextmanager
    def user_context(self, user_id: str | None) -> Iterator[str]:
        resolved = self.normalize_user_id(user_id)
        token = self._current_user_id.set(resolved)
        try:
            yield resolved
        finally:
            self._current_user_id.reset(token)

    @contextmanager
    def agent_instance_context(self, agent_instance_id: str | None) -> Iterator[str]:
        resolved = self.normalize_agent_instance_id(agent_instance_id)
        token = self._current_agent_instance_id.set(resolved)
        try:
            yield resolved
        finally:
            self._current_agent_instance_id.reset(token)


def _normalize(value: str | None, default: str) -> str:
    raw = (value or default).strip()
    cleaned = _USER_SAFE_RE.sub("_", raw)
    return cleaned.strip("._-") or default
