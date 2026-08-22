"""Ephemeral file storage for attachments a human adds at approval time.

Distinct from src/media.py's per-instance signature/contact-photo storage,
which is durable and long-lived. These files exist only for the life of one
pending run: staged when a reviewer uploads a file in the approval screen,
consumed when the run's send tool executes, discarded once the run resolves
(src/run_registry.py calls discard_run_attachments for any non-active status).

Uses the same local/S3 backend abstraction as src/media.py so the storage
location follows the same AGENT_MEDIA_BACKEND configuration, under its own
key prefix.
"""

from __future__ import annotations

import json
import logging
import uuid

from src.config import settings
from src.media import media_root
from src.media_storage import get_backend

logger = logging.getLogger(__name__)

# One JSON blob per run lists the attachment ids that belong to it — the
# backend has no directory listing, so cleanup needs an explicit index.
_MAX_FILENAME_LEN = 255


def _backend():
    return get_backend(local_root=media_root())


def _prefix(run_id: str) -> str:
    return f"run-attachments/{run_id}"


def _index_key(run_id: str) -> str:
    return f"{_prefix(run_id)}/index.json"


def _data_key(run_id: str, attachment_id: str) -> str:
    return f"{_prefix(run_id)}/{attachment_id}.bin"


def _meta_key(run_id: str, attachment_id: str) -> str:
    return f"{_prefix(run_id)}/{attachment_id}.json"


def _read_index(run_id: str) -> list[dict]:
    backend = _backend()
    raw = backend.read(_index_key(run_id))
    if not raw:
        return []
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []


def _write_index(run_id: str, entries: list[dict]) -> None:
    _backend().write(_index_key(run_id), json.dumps(entries).encode("utf-8"))


class AttachmentLimitError(ValueError):
    """A staged upload would exceed the configured size/count cap for the run."""


def staged_attachments(run_id: str) -> list[dict]:
    """Metadata (no bytes) for every file staged on this run, oldest first."""
    return _read_index(run_id)


def save_attachment(run_id: str, filename: str, mime_type: str, data: bytes) -> dict:
    """Store one reviewer-uploaded file for a pending run.

    Raises AttachmentLimitError if it would push the run over
    AGENT_MAX_ATTACHMENT_BYTES (total) or AGENT_MAX_ATTACHMENT_COUNT.
    """
    index = _read_index(run_id)
    if len(index) >= settings.max_attachment_count:
        raise AttachmentLimitError(
            f"This run already has {settings.max_attachment_count} staged attachments."
        )
    total = sum(int(entry.get("size") or 0) for entry in index)
    if total + len(data) > settings.max_attachment_bytes:
        raise AttachmentLimitError(
            f"Staged attachments would exceed the {settings.max_attachment_bytes} byte limit."
        )

    attachment_id = uuid.uuid4().hex
    safe_filename = (filename or "attachment").strip()[:_MAX_FILENAME_LEN] or "attachment"
    entry = {
        "attachment_id": attachment_id,
        "filename": safe_filename,
        "mime_type": mime_type or "application/octet-stream",
        "size": len(data),
    }

    backend = _backend()
    backend.write(_data_key(run_id, attachment_id), data)
    backend.write(_meta_key(run_id, attachment_id), json.dumps(entry).encode("utf-8"))
    index.append(entry)
    _write_index(run_id, index)
    return entry


def load_attachments(run_id: str, attachment_ids: list[str]) -> tuple[list[dict], list[str]]:
    """Resolve staged attachment ids to {filename, mime_type, data} for sending.

    Returns (attachments, notes) — a missing/unreadable id is skipped with a
    note rather than failing the whole send.
    """
    if not attachment_ids:
        return [], []
    backend = _backend()
    index_by_id = {entry["attachment_id"]: entry for entry in _read_index(run_id)}
    attachments: list[dict] = []
    notes: list[str] = []
    for attachment_id in attachment_ids:
        meta = index_by_id.get(attachment_id)
        if meta is None:
            notes.append(f"Skipped an uploaded file: not found for this run ({attachment_id}).")
            continue
        data = backend.read(_data_key(run_id, attachment_id))
        if data is None:
            notes.append(f"Skipped '{meta.get('filename', attachment_id)}': file data missing.")
            continue
        attachments.append(
            {"filename": meta.get("filename"), "mime_type": meta.get("mime_type"), "data": data}
        )
    return attachments, notes


def discard_run_attachments(run_id: str) -> None:
    """Best-effort delete of every file staged for this run, plus the index."""
    try:
        backend = _backend()
        for entry in _read_index(run_id):
            attachment_id = entry.get("attachment_id")
            if not attachment_id:
                continue
            backend.delete(_data_key(run_id, attachment_id))
            backend.delete(_meta_key(run_id, attachment_id))
        backend.delete(_index_key(run_id))
    except Exception as exc:  # pragma: no cover - defensive, cleanup must never raise
        logger.warning(f"run_attachments: cleanup failed for run {run_id}: {exc!r}")
