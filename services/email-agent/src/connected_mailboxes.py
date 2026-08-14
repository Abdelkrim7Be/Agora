from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from src.config import SERVICE_ROOT, settings
from src.tenant import normalize_agent_instance_id, normalize_user_id
from src.token_store import has_stored_token


class MailboxAlreadyConnectedError(ValueError):
    def __init__(self, mailbox: str, existing_instance_id: str):
        super().__init__(
            f"Mailbox {mailbox} is already connected to instance {existing_instance_id}. "
            "Open that instance instead of connecting the same mailbox again."
        )


def _path() -> Path:
    configured = Path(settings.connected_mailboxes_path)
    return configured if configured.is_absolute() else SERVICE_ROOT / configured


def _key(provider: str, mailbox: str) -> str:
    return f"{provider.strip().lower()}:{mailbox.strip().lower()}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read() -> dict:
    path = _path()
    if not path.is_file():
        return {"mailboxes": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {"mailboxes": {}}
    return data if isinstance(data, dict) else {"mailboxes": {}}


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@contextmanager
def _locked() -> Iterator[None]:
    import fcntl

    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def claim_mailbox(provider: str, mailbox: str, user_id: str, agent_instance_id: str) -> dict:
    normalized_provider = provider.strip().lower()
    normalized_mailbox = mailbox.strip().lower()
    if not normalized_mailbox:
        raise ValueError("Could not verify authorized mailbox address.")
    normalized_instance = normalize_agent_instance_id(agent_instance_id)
    normalized_user = normalize_user_id(user_id)
    key = _key(normalized_provider, normalized_mailbox)

    with _locked():
        data = _read()
        mailboxes = data.setdefault("mailboxes", {})
        existing = mailboxes.get(key)
        if isinstance(existing, dict):
            existing_instance = normalize_agent_instance_id(str(existing.get("agent_instance_id") or ""))
            if (
                existing_instance
                and existing_instance != normalized_instance
                and has_stored_token(existing_instance, provider=normalized_provider)
            ):
                raise MailboxAlreadyConnectedError(normalized_mailbox, existing_instance)

        record = {
            "provider": normalized_provider,
            "mailbox": normalized_mailbox,
            "user_id": normalized_user,
            "agent_instance_id": normalized_instance,
            "connected_at": _now(),
        }
        mailboxes[key] = record
        _write(data)
        return record


def release_mailbox(provider: str, agent_instance_id: str) -> bool:
    normalized_provider = provider.strip().lower()
    normalized_instance = normalize_agent_instance_id(agent_instance_id)
    removed = False
    with _locked():
        data = _read()
        mailboxes = data.setdefault("mailboxes", {})
        for key, record in list(mailboxes.items()):
            if not isinstance(record, dict):
                continue
            if (
                str(record.get("provider") or "").strip().lower() == normalized_provider
                and normalize_agent_instance_id(str(record.get("agent_instance_id") or "")) == normalized_instance
            ):
                del mailboxes[key]
                removed = True
        if removed:
            _write(data)
    return removed
