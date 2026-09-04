from __future__ import annotations

from pydantic import BaseModel

from src.contacts import delete_contact, get_contact
from src.cost_tracker import count_costs_for_runs, delete_costs_for_runs
from src.notification_store import erase_notifications_for_subject, list_notifications
from src.retention import _checkpoint_counts, _delete_checkpoints, _zero_counts
from src.run_registry import MAX_RUNS, delete_runs, list_runs
from src.tenant import current_agent_instance_id, normalize_agent_instance_id
from src.token_store import delete_token
from src.trace import count_traces_for_runs, delete_traces_for_runs

# Automates steps 1-5 of the manual right-to-erasure procedure in
# docs/compliance/DATA_RETENTION.md §3. Backups (6) and the audit log (7)
# are deliberately out of scope here — see that doc for why.


class ErasureRequest(BaseModel):
    email: str
    agent_instance_id: str | None = None
    revoke_owner_token: bool = False


def _iter_all_runs(agent_instance_id: str) -> list[dict]:
    """Page through every run for one instance, ignoring the MAX_RUNS list cap.

    list_runs(limit=None) still caps at MAX_RUNS internally; a subject-erasure
    scan has to see every run ever recorded for the instance, not just the most
    recent MAX_RUNS, so this pages with explicit offsets instead.
    """
    runs: list[dict] = []
    offset = 0
    while True:
        page = list_runs(agent_instance_id=agent_instance_id, limit=MAX_RUNS, offset=offset)
        if not page:
            break
        runs.extend(page)
        if len(page) < MAX_RUNS:
            break
        offset += MAX_RUNS
    return runs


def _matching_run_ids(email: str, agent_instance_id: str) -> list[str]:
    normalized = email.strip().lower()
    return [
        run["run_id"]
        for run in _iter_all_runs(agent_instance_id)
        if run.get("run_id") and normalized in (run.get("author") or "").lower()
    ]


def preview_erasure(
    email: str,
    agent_instance_id: str | None = None,
    revoke_owner_token: bool = False,
) -> dict:
    """Compute what erase_subject would remove, without deleting anything."""
    resolved_instance = normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())
    run_ids = _matching_run_ids(email, resolved_instance)
    checkpoint_counts = _checkpoint_counts(run_ids)
    notifications_count = 0
    if revoke_owner_token:
        notifications_count = len(
            list_notifications(unread_only=False, limit=200, user_id=email, agent_instance_id=resolved_instance)
        )
    return {
        "email": email,
        "agent_instance_id": resolved_instance,
        "contact_found": get_contact(email, agent_instance_id=resolved_instance) is not None,
        "token_would_be_revoked": bool(revoke_owner_token),
        "run_ids": run_ids,
        "counts": {
            "runs": len(run_ids),
            "cost_entries": count_costs_for_runs(run_ids, agent_instance_id=resolved_instance),
            "trace_entries": count_traces_for_runs(run_ids, agent_instance_id=resolved_instance),
            **checkpoint_counts,
            "notifications": notifications_count,
        },
    }


def erase_subject(
    email: str,
    agent_instance_id: str | None = None,
    revoke_owner_token: bool = False,
) -> dict:
    """Erase a data subject's personal data for one agent instance.

    revoke_owner_token must be explicitly set — it should only be true when the
    subject IS the mailbox owner being offboarded, not a third-party contact
    whose address merely appears in the mailbox's mail. There is no reliable
    email-to-instance-owner mapping to infer this automatically (see
    docs/compliance/DATA_RETENTION.md §3, step 2).
    """
    resolved_instance = normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())

    contact = get_contact(email, agent_instance_id=resolved_instance)
    contact_deleted = False
    if contact is not None:
        delete_contact(email, agent_instance_id=resolved_instance)
        contact_deleted = True

    token_revoked = False
    notifications_deleted = 0
    if revoke_owner_token:
        token_revoked = delete_token(user_id=email, agent_instance_id=resolved_instance)
        notifications_deleted = erase_notifications_for_subject(email, resolved_instance)

    run_ids = _matching_run_ids(email, resolved_instance)
    deleted = _zero_counts()
    if run_ids:
        checkpoint_deleted = _delete_checkpoints(run_ids)
        deleted["trace_entries"] = delete_traces_for_runs(run_ids, agent_instance_id=resolved_instance)
        deleted["cost_entries"] = delete_costs_for_runs(run_ids, agent_instance_id=resolved_instance)
        deleted["runs"] = delete_runs(run_ids, agent_instance_id=resolved_instance)
        deleted.update(checkpoint_deleted)
    deleted["notifications"] = notifications_deleted

    return {
        "email": email,
        "agent_instance_id": resolved_instance,
        "contact_deleted": contact_deleted,
        "token_revoked": token_revoked,
        "run_ids": run_ids,
        "deleted": deleted,
    }
