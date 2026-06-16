from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from src.config import SERVICE_ROOT

DEFAULT_RUN_INDEX = SERVICE_ROOT / "logs" / "run_index.json"


def _path(path: str | Path | None = None) -> Path:
    if path is None:
        return DEFAULT_RUN_INDEX
    p = Path(path)
    return p if p.is_absolute() else SERVICE_ROOT / p


def _read(path: Path) -> dict:
    if not path.is_file():
        return {"runs": []}
    return json.loads(path.read_text())


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def upsert_run(
    run_id: str,
    status: str,
    email_input: dict | None = None,
    classification: str | None = None,
    pending_action: list | None = None,
    path: str | Path | None = None,
) -> dict:
    index_path = _path(path)
    data = _read(index_path)
    runs = data.setdefault("runs", [])
    existing = next((r for r in runs if r.get("run_id") == run_id), None)
    email_input = email_input or {}
    record = {
        "run_id": run_id,
        "status": status,
        "classification": classification,
        "pending_action": pending_action,
        "subject": email_input.get("subject"),
        "author": email_input.get("author"),
        "email_id": email_input.get("email_id"),
        "gmail_thread_id": email_input.get("gmail_thread_id"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if existing:
        existing.update({k: v for k, v in record.items() if v is not None})
        saved = existing
    else:
        runs.append(record)
        saved = record
    runs.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    _write(index_path, data)
    return saved


def list_runs(status: str | None = None, path: str | Path | None = None) -> list[dict]:
    runs = _read(_path(path)).get("runs", [])
    if status:
        runs = [r for r in runs if r.get("status") == status]
    return runs
