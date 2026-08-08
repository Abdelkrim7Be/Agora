from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from platform_core.runs import RunRegistry

from src.config import SERVICE_ROOT, settings
from src.postgres import tenant_connection
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
)

DEFAULT_RUN_INDEX = SERVICE_ROOT / "logs" / "run_index.json"

# Cap the local JSON index so a long-running dev poller can't grow it unbounded.
# Production deployments use the Postgres backend instead.
MAX_RUNS = 1000

# Statuses where the email is still awaiting a human and is left UNREAD on purpose.
ACTIVE_RUN_STATUSES = ("pending_approval", "security_hold")

# The email agent's own run columns, on top of platform_core.runs.CORE_COLUMNS.
# One list feeds every statement, so a column cannot be read by one and dropped
# by another.
EMAIL_COLUMNS = (
    "run_id",
    "user_id",
    "agent_instance_id",
    "status",
    "classification",
    "pending_action",
    "subject",
    "author",
    "email_id",
    "gmail_thread_id",
    "category",
    "category_display_name",
    "priority",
    "template",
    "workflow_owner",
    "workflow_approver",
    "workflow_route_to",
    "workflow_dept",
    "assignee",
    "created_at",
    "decision",
    "decision_at",
    "error",
    "junk_reason",
    "updated_at",
)

# Workflow routing and assignment are cleared as well as set, so a null must
# overwrite rather than be read as "unchanged".
_OVERWRITE_COLUMNS = (
    "category",
    "category_display_name",
    "priority",
    "template",
    "workflow_owner",
    "workflow_approver",
    "workflow_route_to",
    "workflow_dept",
    "assignee",
    "error",
)


def _path(path: str | Path | None = None) -> Path:
    if path is None:
        return DEFAULT_RUN_INDEX
    p = Path(path)
    return p if p.is_absolute() else SERVICE_ROOT / p


def _connect():
    return tenant_connection()


_registry = RunRegistry(
    table="agent_runs",
    columns=EMAIL_COLUMNS,
    json_columns=("pending_action", "workflow_route_to"),
    overwrite_columns=_OVERWRITE_COLUMNS,
    max_runs=MAX_RUNS,
    resolve_path=_path,
    backend=lambda: settings.run_registry_backend,
    database_url=lambda: settings.database_url,
    # Resolved at call time so the module-level name stays the patchable seam.
    connect=lambda: _connect(),
    normalize_user_id=normalize_user_id,
    normalize_agent_instance_id=normalize_agent_instance_id,
    current_agent_instance_id=current_agent_instance_id,
)

selected_run_registry_backend = _registry.selected_backend
setup_run_registry = _registry.setup
list_runs = _registry.list
get_run = _registry.get
claim_run = _registry.claim
assign_run = _registry.assign
list_runs_before = _registry.list_before
delete_runs = _registry.delete
_read = _registry.read
_postgres_row = _registry._row


def _record(
    run_id: str,
    status: str,
    email_input: dict | None,
    classification: str | None,
    pending_action: list | None,
    user_id: str | None,
    agent_instance_id: str | None,
    created_at: str | None,
    decision: str | None,
    decision_at: str | None,
) -> dict:
    email_input = email_input or {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "user_id": normalize_user_id(user_id or current_user_id()),
        "agent_instance_id": normalize_agent_instance_id(
            agent_instance_id or current_agent_instance_id()
        ),
        "run_id": run_id,
        "status": status,
        "classification": classification,
        "pending_action": pending_action,
        "subject": email_input.get("subject"),
        "author": email_input.get("author"),
        "email_id": email_input.get("email_id"),
        "gmail_thread_id": email_input.get("gmail_thread_id"),
        "category": email_input.get("category"),
        "category_display_name": email_input.get("category_display_name"),
        # priority is NOT NULL in Postgres; callers may pass an explicit None
        # (e.g. security_hold / notify runs with no matched category), so coerce.
        "priority": email_input.get("priority") or "normal",
        "template": email_input.get("template"),
        "workflow_owner": email_input.get("workflow_owner"),
        "workflow_approver": email_input.get("workflow_approver"),
        "workflow_route_to": email_input.get("workflow_route_to") or [],
        "workflow_dept": email_input.get("workflow_dept"),
        "assignee": email_input.get("assignee"),
        "error": email_input.get("error"),
        # Set by the junk gate; None for every run that reached the graph.
        "junk_reason": email_input.get("junk_reason"),
        "created_at": created_at or now,
        "decision": decision,
        "decision_at": decision_at,
        "updated_at": now,
    }


def upsert_run(
    run_id: str,
    status: str,
    email_input: dict | None = None,
    classification: str | None = None,
    pending_action: list | None = None,
    path: str | Path | None = None,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
    created_at: str | None = None,
    decision: str | None = None,
    decision_at: str | None = None,
) -> dict:
    record = _record(
        run_id,
        status,
        email_input,
        classification,
        pending_action,
        user_id,
        agent_instance_id,
        created_at,
        decision,
        decision_at,
    )
    result = _registry.upsert(record, path=path)
    if status not in ACTIVE_RUN_STATUSES:
        # Reviewer-uploaded attachments only need to survive while the run is
        # waiting for a human; once it resolves (sent, ignored, rejected,
        # failed...) they no longer serve a purpose.
        from src.run_attachments import discard_run_attachments

        discard_run_attachments(run_id)
    return result


def find_run_by_email(
    email_id: str,
    path: str | Path | None = None,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> dict | None:
    """Return the most recent run for a Gmail message id, or None.

    Lets the poller avoid reprocessing an email that already has a run: a pending
    or held run must not spawn duplicates every cycle, and a resolved run means the
    message was already handled. list_runs is newest-first, so the first match wins.
    """
    if not email_id:
        return None
    for record in list_runs(path=path, user_id=user_id, agent_instance_id=agent_instance_id):
        if record.get("email_id") == email_id:
            return record
    return None
