from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import uuid

import yaml
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from langgraph.types import Command
from pydantic import BaseModel

from src.config import settings
from src.automation import DEFAULT_RULES_PATH, RulesConfig, load_rules
from src.config import AgentConfig, DEFAULT_CONFIG_PATH, load_config
from src import graph as graph_module
from src.capabilities import current_email_id, current_gmail_thread_id, hitl_approved
from src.graph import overall_workflow, reload_config
from src.poller import poll_history, poll_once
from src.memory import namespace, preferences_text, wrap_preferences
from src.run_registry import ACTIVE_RUN_STATUSES
from src.run_registry import get_run as get_run_record
from src.run_registry import list_runs, setup_run_registry, upsert_run
from src.gmail_sync import get_last_history_id, set_last_history_id, setup_gmail_sync
from src.gmail_client import (
    archive_message,
    gmail_resource,
    list_inbox,
    mark_as_read,
    mark_as_unread,
    trash_message,
)
from src.tenant import (
    agent_instance_context,
    current_agent_instance_id,
    current_user_id,
    resolve_user_id,
    user_context,
)
from src.security_client import fetch_policy
from src.storage import open_graph_storage


async def _watch_renewal_loop() -> None:
    """Register and periodically renew the Gmail push watch from the API process.

    Gmail watches expire after 7 days, so the mailbox must be re-registered well
    inside that window for push delivery to keep working. Runs only when webhooks
    are enabled; ensure_watch also seeds the per-user historyId baseline.
    """
    from src.poller import ensure_watch

    setup_gmail_sync()
    interval = settings.gmail_watch_renew_hours * 3600
    while True:
        try:
            await asyncio.to_thread(ensure_watch)
        except Exception as exc:  # network/credential issues must not kill the API
            print(f"api: gmail watch registration failed: {exc}")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_run_registry()
    setup_gmail_sync()
    async with open_graph_storage() as storage:
        # The graph's nodes are sync, so LangGraph runs them in a threadpool where
        # sync store.get/put works. A future ASYNC node must use aget/aput instead.
        app.state.graph = overall_workflow.compile(
            checkpointer=storage.checkpointer, store=storage.store
        )
        app.state.store = storage.store
        app.state.storage_backend = storage.backend
        watch_task = (
            asyncio.create_task(_watch_renewal_loop())
            if settings.gmail_webhook_enabled
            else None
        )
        try:
            yield
        finally:
            if watch_task is not None:
                watch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watch_task


app = FastAPI(title="email-agent", version="0.1.0", lifespan=lifespan)


def _request_user_id(request: Request) -> str | None:
    return request.headers.get("x-agora-user")


def _request_agent_instance_id(request: Request) -> str | None:
    return request.headers.get("x-agora-agent-instance")


@app.middleware("http")
async def tenant_context_middleware(request: Request, call_next):
    with user_context(_request_user_id(request)):
        with agent_instance_context(_request_agent_instance_id(request)):
            return await call_next(request)


class EmailInput(BaseModel):
    author: str
    to: str
    subject: str
    email_thread: str


class ApprovalInput(BaseModel):
    # Optional override of the action args (e.g. an edited email draft).
    args: dict | None = None


class RespondInput(BaseModel):
    feedback: str


class GmailWebhookInput(BaseModel):
    message: dict = {}
    subscription: str | None = None


class RulesInput(BaseModel):
    rules_yaml: str


class CapabilitiesInput(BaseModel):
    capabilities: dict[str, bool]


class MemoryInput(BaseModel):
    triage_preferences: str
    response_preferences: str


class RunResponse(BaseModel):
    run_id: str
    status: str  # "pending_approval" | "completed" | "failed"
    classification: str | None = None
    error: str | None = None
    pending_action: list | None = None  # list of Agent Inbox request objects when paused


def _thread_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _format(result: dict, run_id: str) -> RunResponse:
    """Turn a graph result into a response — paused on approval, or completed."""
    interrupts = result.get("__interrupt__")
    if interrupts:
        return RunResponse(
            run_id=run_id,
            status="pending_approval",
            pending_action=interrupts[0].value,
        )
    if result.get("email_send_failed"):
        return RunResponse(
            run_id=run_id,
            status="failed",
            classification=result.get("classification_decision", "unknown"),
            error=result.get("email_send_failed"),
        )
    return RunResponse(
        run_id=run_id,
        status="completed",
        classification=result.get("classification_decision", "unknown"),
    )


def _record_response(run: RunResponse, email_input: dict | None = None) -> None:
    upsert_run(
        run.run_id,
        run.status,
        email_input=email_input,
        classification=run.classification,
        pending_action=run.pending_action,
        user_id=current_user_id(),
        agent_instance_id=current_agent_instance_id(),
    )

def _pending_response_after_decision_error(run_id: str, exc: Exception, action: str) -> RunResponse | None:
    record = get_run_record(
        run_id,
        user_id=current_user_id(),
        agent_instance_id=current_agent_instance_id(),
    )
    if not record or record.get("status") != "pending_approval":
        return None
    print(f"api: {action} failed for run {run_id}; keeping pending approval: {exc}")
    return RunResponse(
        run_id=run_id,
        status="pending_approval",
        classification=record.get("classification"),
        pending_action=record.get("pending_action"),
        error=f"Could not complete {action}; draft is still pending. {type(exc).__name__}: {exc}",
    )


def _pending_action(record: dict) -> tuple[str, dict] | None:
    pending = record.get("pending_action") or []
    if not pending or not isinstance(pending[0], dict):
        return None
    request = pending[0].get("action_request") or {}
    action = request.get("action")
    args = request.get("args") or {}
    if not action or not isinstance(args, dict):
        return None
    return action, args


def _record_email_input(record: dict) -> dict:
    return {
        "subject": record.get("subject"),
        "author": record.get("author"),
        "email_id": record.get("email_id"),
        "gmail_thread_id": record.get("gmail_thread_id"),
    }


def _complete_pending_rejection(run_id: str) -> RunResponse | None:
    record = get_run_record(
        run_id,
        user_id=current_user_id(),
        agent_instance_id=current_agent_instance_id(),
    )
    if not record or record.get("status") != "pending_approval":
        return None
    response = RunResponse(
        run_id=run_id,
        status="completed",
        classification=record.get("classification"),
    )
    _record_response(response, email_input=_record_email_input(record))
    return response


def _execute_pending_action(run_id: str, args_override: dict | None = None) -> RunResponse | None:
    record = get_run_record(
        run_id,
        user_id=current_user_id(),
        agent_instance_id=current_agent_instance_id(),
    )
    if not record or record.get("status") != "pending_approval":
        return None
    pending = _pending_action(record)
    if pending is None:
        return None
    action, args = pending
    if args_override is not None:
        args = args_override
    tool = graph_module.tools_by_name_map.get(action)
    if tool is None:
        response = RunResponse(
            run_id=run_id,
            status="failed",
            classification=record.get("classification"),
            error=f"The '{action}' action is not available.",
        )
        _record_response(response, email_input=_record_email_input(record))
        return response

    email_id_token = current_email_id.set(record.get("email_id"))
    thread_id_token = current_gmail_thread_id.set(record.get("gmail_thread_id"))
    approval_token = hitl_approved.set(True)
    try:
        tool.invoke(args)
    except Exception as exc:
        response = RunResponse(
            run_id=run_id,
            status="failed",
            classification=record.get("classification"),
            error=f"The '{action}' action could not be completed: {exc}.",
        )
    else:
        response = RunResponse(
            run_id=run_id,
            status="completed",
            classification=record.get("classification"),
        )
    finally:
        hitl_approved.reset(approval_token)
        current_gmail_thread_id.reset(thread_id_token)
        current_email_id.reset(email_id_token)

    _record_response(response, email_input=_record_email_input(record))
    return response


async def _require_run(graph, run_id: str) -> dict:
    if get_run_record(
        run_id,
        user_id=current_user_id(),
        agent_instance_id=current_agent_instance_id(),
    ) is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    config = _thread_config(run_id)
    state = await graph.aget_state(config)
    if not state.values:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    return config


def _message_summary(message) -> dict:
    if isinstance(message, dict):
        role = message.get("role", message.get("type", "message"))
        content = message.get("content", "")
        tool_calls = message.get("tool_calls", [])
    else:
        role = getattr(message, "type", message.__class__.__name__)
        content = getattr(message, "content", "")
        tool_calls = getattr(message, "tool_calls", []) or []
    return {
        "role": role,
        "content": content,
        "tool_calls": tool_calls,
    }


def _run_detail(values: dict, run_id: str) -> dict:
    email_input = values.get("email_input", {})
    return {
        "run_id": run_id,
        "status": (
            "pending_approval"
            if values.get("__interrupt__")
            else "failed"
            if values.get("email_send_failed")
            else "completed"
        ),
        "classification": values.get("classification_decision"),
        "error": values.get("email_send_failed"),
        "email": {
            "author": email_input.get("author"),
            "to": email_input.get("to"),
            "subject": email_input.get("subject"),
            "email_id": email_input.get("email_id"),
            "gmail_thread_id": email_input.get("gmail_thread_id"),
        },
        "security": email_input.get("security"),
        "automation": email_input.get("automation"),
        "timeline": [_message_summary(m) for m in values.get("messages", [])],
    }


def _decode_pubsub_data(message: dict) -> dict:
    data = message.get("data")
    if not data:
        return {}
    padded = data + "=" * (-len(data) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


def _require_webhook_secret(request: Request) -> None:
    secret = settings.gmail_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="GMAIL_WEBHOOK_SECRET is required when Gmail webhooks are enabled")
    provided = request.query_params.get("token") or request.headers.get("x-gmail-webhook-token")
    if provided != secret:
        raise HTTPException(status_code=403, detail="Invalid Gmail webhook token")


@app.post("/webhooks/gmail", status_code=202)
async def gmail_webhook(request: Request, body: GmailWebhookInput) -> dict:
    if not settings.gmail_webhook_enabled:
        return {"accepted": False, "reason": "gmail webhooks disabled"}
    _require_webhook_secret(request)

    try:
        payload = _decode_pubsub_data(body.message)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Pub/Sub message data") from exc

    pushed_history_id = str(payload.get("historyId") or "")
    if not pushed_history_id:
        raise HTTPException(status_code=400, detail="Missing Gmail historyId")

    email_address = payload.get("emailAddress")
    with user_context(resolve_user_id(email_address)):
        # Gmail's history.list returns changes *after* startHistoryId, so the message
        # that fired this push is found by querying from our previously stored baseline
        # — not the pushed id. Without a baseline (watch not yet bootstrapped) we seed
        # it and wait for the next push, which then covers everything since now.
        baseline = get_last_history_id()
        if baseline is None:
            set_last_history_id(pushed_history_id)
            return {"accepted": True, "history_id": pushed_history_id, "outcomes": [], "synced": False}
        try:
            outcomes = await poll_history(request.app.state.graph, baseline)
        except Exception:
            # Stale baseline (history older than ~1 week is purged by Gmail). Reset
            # forward and ack so Pub/Sub stops retrying an unrecoverable window.
            set_last_history_id(pushed_history_id)
            return {"accepted": True, "history_id": pushed_history_id, "outcomes": [], "synced": False}
        set_last_history_id(pushed_history_id)

    return {"accepted": True, "history_id": pushed_history_id, "outcomes": outcomes}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "storage_backend": settings.storage_backend}


@app.get("/rules")
async def get_rules() -> dict:
    rules_yaml = DEFAULT_RULES_PATH.read_text() if DEFAULT_RULES_PATH.is_file() else "enabled: false\n"
    return {
        "rules_yaml": rules_yaml,
        "parsed": load_rules().model_dump(),
    }


@app.put("/rules")
async def update_rules(body: RulesInput) -> dict:
    data = yaml.safe_load(body.rules_yaml) or {}
    if data.get("rules") is None:
        data["rules"] = []
    parsed = RulesConfig(**data)  # validate before persisting
    # Persist the user's raw YAML verbatim so comments/formatting survive a round-trip.
    DEFAULT_RULES_PATH.write_text(body.rules_yaml)
    return {
        "rules_yaml": body.rules_yaml,
        "parsed": parsed.model_dump(),
    }


@app.get("/capabilities")
async def get_capabilities() -> dict:
    return {"capabilities": load_config().capabilities}


@app.put("/capabilities")
async def update_capabilities(body: CapabilitiesInput) -> dict:
    current = load_config().model_dump()
    current["capabilities"] = body.capabilities
    cfg = AgentConfig(**current)
    DEFAULT_CONFIG_PATH.write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False))
    reload_config()  # make the toggle live in this process (graph reads module globals)
    return {"capabilities": cfg.capabilities}


@app.get("/policy")
async def get_policy() -> dict:
    # The policy lives in the (separate) security service — fetch it over HTTP rather
    # than reaching for a file that isn't in this container.
    return await fetch_policy()


@app.get("/config")
async def get_agent_config() -> dict:
    return load_config().model_dump()


@app.put("/config")
async def update_agent_config(body: dict) -> dict:
    cfg = AgentConfig(**body)
    DEFAULT_CONFIG_PATH.write_text(yaml.safe_dump(cfg.model_dump(), sort_keys=False))
    reload_config()  # persona/triage edits take effect without a restart (this process)
    return cfg.model_dump()


@app.get("/memory")
async def get_preferences(request: Request) -> dict:
    cfg = load_config()
    store = request.app.state.store
    triage = await store.aget(namespace("triage_preferences"), "user_preferences")
    response = await store.aget(namespace("response_preferences"), "user_preferences")
    return {
        "triage_preferences": preferences_text(triage.value) if triage else cfg.agent.triage_instructions,
        "response_preferences": preferences_text(response.value) if response else cfg.agent.response_preferences,
    }


@app.put("/memory")
async def update_preferences(request: Request, body: MemoryInput) -> dict:
    # AsyncSqliteStore: must use the async API on the event loop (sync calls raise).
    store = request.app.state.store
    await store.aput(namespace("triage_preferences"), "user_preferences", wrap_preferences(body.triage_preferences))
    await store.aput(namespace("response_preferences"), "user_preferences", wrap_preferences(body.response_preferences))
    return {
        "triage_preferences": body.triage_preferences,
        "response_preferences": body.response_preferences,
    }


@app.get("/runs")
async def runs(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    # Fetch one extra row to tell the UI whether a next page exists.
    page = list_runs(
        status=status,
        user_id=current_user_id(),
        agent_instance_id=current_agent_instance_id(),
        limit=limit + 1,
        offset=offset,
    )
    has_more = len(page) > limit
    return {"runs": page[:limit], "limit": limit, "offset": offset, "has_more": has_more}


@app.post("/sync")
async def sync_unread(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    """Process unread Gmail messages now so validation reflects fresh mail."""
    user_id = current_user_id()
    try:
        resource = await asyncio.to_thread(gmail_resource, user_id)
    except Exception as exc:
        print(f"api: gmail sync unavailable for user {user_id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Gmail sync is unavailable. Check OAuth credentials and container network access.",
        ) from exc

    try:
        outcomes = await poll_once(request.app.state.graph, resource=resource, max_results=limit)
    except Exception as exc:
        print(f"api: gmail sync failed for user {user_id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail=f"Gmail sync failed: {type(exc).__name__}: {exc}",
        ) from exc
    return {"outcomes": outcomes}


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


@app.get("/inbox")
async def inbox(limit: int = Query(default=25, ge=1, le=100)) -> dict:
    """List the tenant's recent inbox messages with the agent's verdict attached.

    The Gmail calls are blocking (googleapiclient), so they run in a worker thread to
    keep the event loop free. Each message is matched to an agent run by Gmail message
    id so the UI can show the classification and link straight to the run.
    """
    user_id = current_user_id()
    try:
        resource = await asyncio.to_thread(gmail_resource, user_id)
        messages = await asyncio.to_thread(list_inbox, limit, resource)
    except Exception as exc:
        print(f"api: gmail inbox unavailable for user {user_id}: {exc}")
        runs = await asyncio.to_thread(list_runs, user_id=user_id, agent_instance_id=current_agent_instance_id(), limit=500)
        return {
            "messages": _fallback_inbox_messages(runs, limit),
            "warning": (
                "Gmail inbox is unavailable. Check OAuth credentials and container network access. "
                "Showing last known agent messages."
            ),
        }
    runs = await asyncio.to_thread(list_runs, user_id=user_id, agent_instance_id=current_agent_instance_id(), limit=500)
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


async def _inbox_action(fn, msg_id: str, action: str) -> dict:
    try:
        resource = await asyncio.to_thread(gmail_resource, current_user_id())
        await asyncio.to_thread(fn, msg_id, resource)
    except Exception as exc:
        print(f"api: gmail inbox action {action} unavailable for {msg_id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Gmail inbox is unavailable. Check OAuth credentials and container network access.",
        ) from exc
    return {"ok": True, "msg_id": msg_id, "action": action}


@app.post("/inbox/{msg_id}/archive")
async def inbox_archive(msg_id: str) -> dict:
    return await _inbox_action(archive_message, msg_id, "archive")


@app.post("/inbox/{msg_id}/trash")
async def inbox_trash(msg_id: str) -> dict:
    return await _inbox_action(trash_message, msg_id, "trash")


@app.post("/inbox/{msg_id}/read")
async def inbox_read(msg_id: str) -> dict:
    return await _inbox_action(mark_as_read, msg_id, "read")


@app.post("/inbox/{msg_id}/unread")
async def inbox_unread(msg_id: str) -> dict:
    return await _inbox_action(mark_as_unread, msg_id, "unread")


@app.get("/run/{run_id}", response_model=RunResponse)
async def get_run(request: Request, run_id: str) -> RunResponse:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    state = await graph.aget_state(config)
    return _format(state.values, run_id)


@app.get("/run/{run_id}/detail")
async def get_run_detail(request: Request, run_id: str) -> dict:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    state = await graph.aget_state(config)
    return _run_detail(state.values, run_id)


@app.post("/run", response_model=RunResponse)
async def run(request: Request, email: EmailInput) -> RunResponse:
    graph = request.app.state.graph
    run_id = str(uuid.uuid4())
    email_input = email.model_dump()
    result = await graph.ainvoke(
        {"email_input": email_input}, _thread_config(run_id)
    )
    response = _format(result, run_id)
    _record_response(response, email_input)
    return response


@app.post("/run/{run_id}/approve", response_model=RunResponse)
async def approve(request: Request, run_id: str, approval: ApprovalInput) -> RunResponse:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    try:
        result = await graph.ainvoke(
            Command(resume={"type": "approve", "args": approval.args}), config
        )
    except Exception as exc:
        response = _execute_pending_action(run_id, approval.args)
        if response is not None:
            print(f"api: approve graph resume failed for run {run_id}; used pending action fallback: {exc}")
            return response
        response = _pending_response_after_decision_error(run_id, exc, "approve")
        if response is not None:
            return response
        raise
    response = _format(result, run_id)
    _record_response(response)
    return response


@app.post("/run/{run_id}/reject", response_model=RunResponse)
async def reject(request: Request, run_id: str) -> RunResponse:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    try:
        result = await graph.ainvoke(
            Command(resume={"type": "reject"}), config
        )
    except Exception as exc:
        response = _complete_pending_rejection(run_id)
        if response is not None:
            print(f"api: reject graph resume failed for run {run_id}; completed pending rejection fallback: {exc}")
            return response
        response = _pending_response_after_decision_error(run_id, exc, "reject")
        if response is not None:
            return response
        raise
    response = _format(result, run_id)
    _record_response(response)
    return response


@app.post("/run/{run_id}/respond", response_model=RunResponse)
async def respond(request: Request, run_id: str, body: RespondInput) -> RunResponse:
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    try:
        result = await graph.ainvoke(
            Command(resume=[{"type": "response", "args": body.feedback}]), config
        )
    except Exception as exc:
        response = _pending_response_after_decision_error(run_id, exc, "regenerate draft")
        if response is not None:
            return response
        raise
    response = _format(result, run_id)
    _record_response(response)
    return response
