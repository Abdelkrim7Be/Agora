from __future__ import annotations

import asyncio

from datetime import (
    datetime,
    timezone,
)
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)
from src import graph as graph_module
from src.automation import load_rules
from src.mail import get_provider
from src.poller import process_message_with_retry
from src.run_registry import (
    delete_runs,
    find_run_by_email,
    get_run as get_run_record,
    list_runs,
)
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
)
from src.api_shared import (
    _normalize_dept,
    _request_user_dept,
    _request_user_id,
    _require_dept_access,
    _require_instance_role,
)

import logging
import os
import threading
import time

from pydantic import BaseModel

from src.run_registry import ACTIVE_RUN_STATUSES
from src.shared_cache import cache_delete_prefix, cache_get_json, cache_set_json
from src.tenant import agent_instance_context, user_context
from src.api_shared import _run_timestamp_at_or_after

logger = logging.getLogger(__name__)

router = APIRouter()


_INBOX_CACHE: dict[tuple, tuple[float, list[dict]]] = {}


_INBOX_CACHE_TTL_SECONDS = float(os.getenv("AGENT_INBOX_CACHE_TTL_SECONDS", "60"))


_INBOX_STALE_SECONDS = float(os.getenv("AGENT_INBOX_STALE_SECONDS", "900"))


_INBOX_CACHE_LOCK = threading.Lock()


_INBOX_REFRESHING: set[tuple] = set()


def _inbox_cache_key(key: tuple) -> str:
    """Redis key for a cache tuple: (user_id, agent_instance_id, mailbox, limit)."""
    return "agora:inbox:" + ":".join(str(part) for part in key)


def _inbox_cache_prefix(user_id: str | None, agent_instance_id: str | None) -> str:
    return f"agora:inbox:{user_id}:{agent_instance_id}:"


def _fallback_inbox_messages(runs: list[dict], limit: int) -> list[dict]:
    """Build inbox rows from agent-known runs when Gmail is temporarily unreachable."""
    messages: list[dict] = []
    seen: set[str] = set()
    for record in runs:
        email_id = record.get("email_id")
        if not email_id or email_id in seen:
            continue
        seen.add(email_id)
        messages.append(
            {
                "id": email_id,
                "thread_id": record.get("gmail_thread_id"),
                "from": record.get("author") or "Unknown",
                "subject": record.get("subject") or "(no subject)",
                "snippet": "Gmail is temporarily unavailable; showing the last agent-known message.",
                "date": record.get("updated_at") or "",
                "unread": record.get("status") in ACTIVE_RUN_STATUSES,
                "run_id": record.get("run_id"),
                "run_status": record.get("status"),
                "classification": record.get("classification"),
                "stale": True,
            }
        )
        if len(messages) >= limit:
            break
    return messages


def _inbox_cache_get(key: tuple) -> tuple[list[dict] | None, bool]:
    """Return (messages, is_stale). Stale means "show this now, refresh behind"."""
    if _INBOX_CACHE_TTL_SECONDS <= 0:
        return None, False
    # Shared first: the poller mutates the mailbox in its own process, and only a
    # shared entry can be invalidated by whichever process did the mutating.
    shared = cache_get_json(_inbox_cache_key(key))
    if shared is not None:
        return shared, False
    with _INBOX_CACHE_LOCK:
        entry = _INBOX_CACHE.get(key)
        if entry is None:
            return None, False
        stored_at, messages = entry
        age = time.time() - stored_at
        if age > _INBOX_STALE_SECONDS:
            _INBOX_CACHE.pop(key, None)
            return None, False
        return messages, age > _INBOX_CACHE_TTL_SECONDS


def _inbox_cache_put(key: tuple, messages: list[dict]) -> None:
    if _INBOX_CACHE_TTL_SECONDS <= 0:
        return
    # The shared copy expires at the TTL; the local one is kept for the whole
    # stale window so an expired entry is still there to serve immediately.
    cache_set_json(_inbox_cache_key(key), messages, _INBOX_CACHE_TTL_SECONDS)
    with _INBOX_CACHE_LOCK:
        _INBOX_CACHE[key] = (time.time(), [dict(message) for message in messages])


def _inbox_cache_clear(user_id: str | None = None, agent_instance_id: str | None = None) -> None:
    """Drop cached listings after the mailbox is mutated."""
    cache_delete_prefix(
        "agora:inbox:" if user_id is None and agent_instance_id is None
        else _inbox_cache_prefix(user_id, agent_instance_id)
    )
    with _INBOX_CACHE_LOCK:
        if user_id is None and agent_instance_id is None:
            _INBOX_CACHE.clear()
            return
        for key in [k for k in _INBOX_CACHE if k[0] == user_id and k[1] == agent_instance_id]:
            _INBOX_CACHE.pop(key, None)


def _sent_row(item: dict) -> dict:
    """One row of the sent mailbox, shaped like an inbox row so the view is shared."""
    return {
        "id": item.get("id"),
        "thread_id": item.get("thread_id"),
        "from": item.get("to", ""),
        "to": item.get("to", ""),
        "subject": item.get("subject", ""),
        "snippet": item.get("body", "")[:240],
        "date": item.get("date", ""),
        "unread": False,
        "mailbox": "sent",
    }


def _schedule_inbox_refresh(cache_key: tuple, mailbox: str, limit: int) -> None:
    """Re-read the mailbox behind a stale response, once per key at a time."""
    with _INBOX_CACHE_LOCK:
        if cache_key in _INBOX_REFRESHING:
            return
        _INBOX_REFRESHING.add(cache_key)

    user_id, instance_id, _, _ = cache_key

    async def refresh() -> None:
        try:
            with user_context(user_id), agent_instance_context(instance_id):
                provider = get_provider()
                if mailbox == "sent":
                    fresh = await asyncio.to_thread(provider.fetch_sent, limit)
                    messages = [_sent_row(item) for item in fresh if item.get("id")]
                else:
                    messages = await asyncio.to_thread(provider.list_inbox, limit)
                    for message in messages:
                        message["mailbox"] = "inbox"
                _inbox_cache_put(cache_key, messages)
        except Exception as exc:
            # A failed refresh leaves the stale entry in place, which is the
            # whole point: the view keeps working while the mailbox is away.
            logger.warning("background inbox refresh failed for %s: %s", instance_id, exc)
        finally:
            with _INBOX_CACHE_LOCK:
                _INBOX_REFRESHING.discard(cache_key)

    asyncio.create_task(refresh())


async def _inbox_unavailable(exc: Exception, user_id: str, user_dept: str | None, limit: int, mailbox: str = "inbox") -> dict:
    """Gmail is unreachable: fall back to the last runs this instance recorded."""
    logger.warning(f"api: gmail inbox unavailable for user {user_id}: {exc}")
    if mailbox == "sent":
        return {
            "messages": [],
            "warning": "Sent mail is unavailable. Check OAuth credentials and container network access.",
        }
    runs = await asyncio.to_thread(
        list_runs, user_id=None, agent_instance_id=current_agent_instance_id(), limit=500
    )
    if user_dept:
        runs = [
            r for r in runs
            if not r.get("workflow_dept") or _normalize_dept(r.get("workflow_dept")) == _normalize_dept(user_dept)
        ]
    return {
        "messages": _fallback_inbox_messages(runs, limit),
        "warning": (
            "Gmail inbox is unavailable. Check OAuth credentials and container network access. "
            "Showing last known agent messages."
        ),
    }


async def _inbox_with_verdicts(messages: list[dict], user_dept: str | None) -> dict:
    """Attach each message's agent run, so a cached listing still shows fresh verdicts."""
    runs = await asyncio.to_thread(
        list_runs, user_id=None, agent_instance_id=current_agent_instance_id(), limit=500
    )
    if user_dept:
        runs = [
            r for r in runs
            if not r.get("workflow_dept") or _normalize_dept(r.get("workflow_dept")) == _normalize_dept(user_dept)
        ]
    by_email: dict[str, dict] = {}
    for record in runs:
        email_id = record.get("email_id")
        if email_id and email_id not in by_email:
            by_email[email_id] = record
    for message in messages:
        record = by_email.get(message["id"])
        if record:
            message["run_id"] = record["run_id"]
            message["run_status"] = record["status"]
            message["classification"] = record.get("classification")
    return {"messages": messages}


async def _inbox_action(method_name: str, msg_id: str, action: str) -> dict:
    try:
        provider = get_provider()
        await asyncio.to_thread(getattr(provider, method_name), msg_id)
    except Exception as exc:
        logger.warning(f"api: gmail inbox action {action} unavailable for {msg_id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Gmail inbox is unavailable. Check OAuth credentials and container network access.",
        ) from exc
    # Archiving, trashing or flipping read state changes what the listing should
    # show, so the cached copy is dropped rather than left to expire.
    _inbox_cache_clear(current_user_id(), current_agent_instance_id())
    return {"ok": True, "msg_id": msg_id, "action": action}


class AssignInput(BaseModel):
    assignee: str | None

@router.get("/drafts")
async def drafts(
    category: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    q: str | None = Query(default=None),
    since: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    runs = list_runs(
        status="pending_approval",
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
        limit=500,
    )
    if category:
        runs = [run for run in runs if run.get("category") == category]
    if priority:
        runs = [run for run in runs if run.get("priority") == priority]
    if q and q.strip():
        needle = q.strip().lower()
        runs = [
            run for run in runs
            if needle in str(run.get("author") or "").lower()
            or needle in str(run.get("subject") or "").lower()
        ]
    if since and since.strip():
        try:
            since_dt = datetime.fromisoformat(since.strip().replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            since_dt = since_dt.astimezone(timezone.utc)
        except ValueError:
            raise HTTPException(status_code=422, detail="since must be an ISO date or datetime")
        runs = [run for run in runs if _run_timestamp_at_or_after(run, since_dt)]
    order = {"urgent": 0, "normal": 1, "low": 2}
    runs.sort(key=lambda run: (order.get(run.get("priority") or "normal", 1), run.get("updated_at", "")))
    return {
        "agent_instance_id": current_agent_instance_id(),
        "drafts": runs[:limit],
        "limit": limit,
    }


@router.get("/inbox")
async def inbox(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    refresh: bool = Query(default=False),
    mailbox: str = Query(default="inbox", pattern="^(inbox|sent)$"),
) -> dict:
    """List the tenant's recent inbox messages with the agent's verdict attached.

    The Gmail calls are blocking (googleapiclient), so they run in a worker thread to
    keep the event loop free. Each message is matched to an agent run by Gmail message
    id so the UI can show the classification and link straight to the run.

    The Gmail half of that is a network round trip to Google — a list call plus a
    metadata batch, measured at roughly four seconds for fifty messages — and it
    was being paid on every single visit to the view, so opening Messages always
    meant watching a spinner. The listing is cached per instance for a short
    window and served immediately; `refresh=true` forces a re-read. Run verdicts
    are re-attached on every request regardless, since those come from the local
    registry in milliseconds and are what actually changes minute to minute.
    """
    user_id = current_user_id()
    user_dept = _request_user_dept(request)
    cache_key = (user_id, current_agent_instance_id(), mailbox, limit)
    cached, is_stale = (None, False) if refresh else _inbox_cache_get(cache_key)
    if cached is not None:
        messages = [dict(message) for message in cached]
        if is_stale:
            # Hand back what we have and go find out what changed, rather than
            # making the person wait on Google to be told mostly the same thing.
            _schedule_inbox_refresh(cache_key, mailbox, limit)
    else:
        try:
            provider = get_provider()
            if mailbox == "sent":
                sent = await asyncio.to_thread(provider.fetch_sent, limit)
                messages = [_sent_row(item) for item in sent if item.get("id")]
            else:
                messages = await asyncio.to_thread(provider.list_inbox, limit)
                for message in messages:
                    message["mailbox"] = "inbox"
            _inbox_cache_put(cache_key, messages)
        except Exception as exc:
            return await _inbox_unavailable(exc, user_id, user_dept, limit, mailbox)
    return await _inbox_with_verdicts(messages, user_dept)


@router.post("/inbox/{msg_id}/archive")
async def inbox_archive(msg_id: str) -> dict:
    return await _inbox_action("archive_message", msg_id, "archive")


@router.post("/inbox/{msg_id}/trash")
async def inbox_trash(msg_id: str) -> dict:
    return await _inbox_action("trash_message", msg_id, "trash")


@router.post("/inbox/{msg_id}/read")
async def inbox_read(msg_id: str) -> dict:
    return await _inbox_action("mark_as_read", msg_id, "read")


@router.post("/inbox/{msg_id}/unread")
async def inbox_unread(msg_id: str) -> dict:
    return await _inbox_action("mark_as_unread", msg_id, "unread")


@router.post("/inbox/{msg_id}/force-agent")
async def inbox_force_agent(request: Request, msg_id: str) -> dict:
    """Mark a message unread, clear its previous run, and process it immediately."""
    provider = get_provider()
    try:
        await asyncio.to_thread(provider.mark_as_unread, msg_id)
    except Exception as exc:
        logger.warning(f"api: gmail force-agent unavailable for {msg_id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Gmail inbox is unavailable. Check OAuth credentials and container network access.",
        ) from exc

    existing = await asyncio.to_thread(
        find_run_by_email,
        msg_id,
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
    )
    cleared = 0
    if existing:
        cleared = await asyncio.to_thread(
            delete_runs,
            [existing["run_id"]],
            agent_instance_id=current_agent_instance_id(),
        )
    graph = getattr(request.app.state, "graph", graph_module.graph)
    outcome = await process_message_with_retry(graph, msg_id, provider, load_rules())
    _inbox_cache_clear(current_user_id(), current_agent_instance_id())
    return {
        "ok": True,
        "msg_id": msg_id,
        "cleared_runs": cleared,
        "outcome": {"message_id": outcome[0], "status": outcome[1], "run_id": outcome[2]},
    }


@router.post("/inbox/{run_id}/claim")
async def claim_run_endpoint(request: Request, run_id: str) -> dict:
    _require_instance_role(request, "approver")
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    _require_dept_access(request, record)
    from src.run_registry import assign_run
    assignee = _request_user_id(request)
    result = await asyncio.to_thread(
        assign_run,
        run_id=run_id,
        assignee=assignee,
        agent_instance_id=current_agent_instance_id(),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"ok": True, "assignee": assignee}


@router.post("/inbox/{run_id}/assign")
async def assign_run_endpoint(request: Request, run_id: str, body: AssignInput) -> dict:
    _require_instance_role(request, "approver")
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    _require_dept_access(request, record)
    from src.run_registry import assign_run
    result = await asyncio.to_thread(
        assign_run,
        run_id=run_id,
        assignee=body.assignee,
        agent_instance_id=current_agent_instance_id(),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"ok": True, "assignee": body.assignee}
