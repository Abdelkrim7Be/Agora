from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import traceback
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import yaml
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import (
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from langgraph.types import Command
from pydantic import (
    BaseModel,
    Field,
)

logger = logging.getLogger(__name__)

from src.config import settings, validate_gateway_shared_secret, validate_gmail_webhook_config, validate_live_send_config, validate_model_redaction
from src.cost_tracker import list_costs, setup_cost_tracker, summarize as summarize_costs
from src.trace import list_traces, setup_trace_store
from src.dlq import claim_dead_letter, get_dead_letter, list_dead_letters, record_dead_letter, setup_dlq
from src.metrics import render_metrics
from src.manifest import build_manifest
from src.categories import (
    CategoriesConfig,
    Category,
    DEFAULT_CATEGORIES_PATH,
    dump_categories,
    load_categories,
)
from src.automation import (
    load_escalation_state,
    workflow_sla_snapshot,
)
from src.analytics import summarize as summarize_analytics
from src.config import (
    AgentConfig,
    DEFAULT_CONFIG_PATH,
    load_config,
)
from src import graph as graph_module
from src.capabilities import current_email_id, current_gmail_thread_id, hitl_approved
from src.graph import overall_workflow, reload_config
from src.instance_config import write_instance_text
from src.poller import (
    gmail_rate_limit_pause_remaining,
    poll_history,
    poll_once,
)
from src.memory import (
    ORIGIN_DEFAULT,
    ORIGIN_LEARNED,
    ORIGIN_MANUAL,
    namespace,
    preferences_origin,
    preferences_text,
    wrap_preferences,
)
from src.roles import (
    RoleConflictError,
    RoleNotFoundError,
    create_role,
    delete_role,
    list_roles,
    normalize_role_key,
    update_role,
)
from src.run_registry import ACTIVE_RUN_STATUSES
from src.run_registry import get_run as get_run_record
from src.run_registry import (
    list_runs,
    setup_run_registry,
    upsert_run,
)
from src.gmail_sync import get_last_history_id, history_id_is_newer, set_last_history_id, setup_gmail_sync
from src.health import aggregate_health
from src.runtime_settings import load_runtime_settings
from src.gdpr import ErasureRequest, erase_subject, preview_erasure
from src.migrate import upgrade_to_head
from src.postgres import validate_runtime_role
from src.token_store import validate_token_security
from src.sync_status import (
    get_status as get_sync_status,
    public_error_message as public_sync_error_message,
    record_failure as record_sync_failure,
    record_success as record_sync_success,
    set_paused as set_sync_paused,
    setup_sync_status,
    _json_update as _sync_json_update,
    _pg_update as _sync_pg_update,
    _resolve as _sync_resolve,
)
from src.run_registry import selected_run_registry_backend as _selected_run_registry_backend
from src.outlook_oauth import (
    OUTLOOK_SCOPES,
    build_authorization_url as build_outlook_authorization_url,
    build_state as build_outlook_oauth_state,
    exchange_code_for_token as exchange_outlook_oauth_code,
    revoke_outlook_token,
    validate_state as validate_outlook_oauth_state,
)
from src.mail.setting import get_mail_provider, set_mail_provider
from src.gmail_oauth import (
    build_authorization_url as build_gmail_authorization_url,
    build_state as build_gmail_oauth_state,
    exchange_code_for_token as exchange_gmail_oauth_code,
    revoke_gmail_token,
    validate_state as validate_gmail_oauth_state,
)
from src.gmail_client import GMAIL_SCOPES
from src.mail import get_provider
from src.tenant import (
    agent_instance_context,
    current_agent_instance_id,
    current_user_id,
    resolve_user_id,
    user_context,
)
from src.api_shared import (
    _bypasses_gateway_secret,
    _run_timestamp_at_or_after,
    _current_categories,
    _normalize_dept,
    _now_iso,
    _gateway_secret_is_valid,
    _request_agent_instance_id,
    _request_user_dept,
    _request_user_id,
    _require_dept_access,
    _require_instance_role,
    _serialize_role,
)
from src.storage import open_graph_storage
from src.style_learning import analyze_style, build_style_text, parse_style_text
from src.run_attachments import AttachmentLimitError
from src.run_attachments import save_attachment as save_run_attachment
from src.run_attachments import staged_attachments as staged_run_attachments
from src.ai_assist import TONES, adjust_tone, summarize_thread
from src.memory_summary import MEMORY_KINDS, memory_items, remove_item, summarize_kind
from src.persona import Persona, compiled_preview, load_persona, save_persona, suggest_persona
from src.instance_setup import get_setup, run_pipeline_inline, start_setup


async def _watch_renewal_loop() -> None:
    """Register and periodically renew the Gmail push watch from the API process.

    Gmail watches expire after 7 days, so the mailbox must be re-registered well
    inside that window for push delivery to keep working. Runs only when webhooks
    are enabled; ensure_watch also seeds the per-user historyId baseline.
    Renewal is expiration-driven: ensure_watches skips instances whose recorded
    watch expiration is still beyond the renewal margin, so this loop can check
    frequently without spamming the Gmail API.
    """
    from src.poller import ensure_watches

    setup_gmail_sync()
    check_interval = min(3600.0, settings.gmail_watch_renew_margin_hours * 1800)
    while True:
        try:
            await asyncio.to_thread(ensure_watches)
        except Exception as exc:  # network/credential issues must not kill the API
            logger.warning(f"api: gmail watch registration failed: {exc}")
            record_sync_failure(str(exc))
        await asyncio.sleep(check_interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_model_redaction()
    validate_gateway_shared_secret()
    validate_gmail_webhook_config()
    validate_live_send_config()
    validate_token_security()
    upgrade_to_head()
    validate_runtime_role()
    setup_run_registry()
    setup_gmail_sync()
    setup_sync_status()
    setup_cost_tracker()
    setup_trace_store()
    setup_dlq()
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

# Domain routers. Imported after `app` exists so a router module never has to
# import this one; shared request helpers live in `src.api_shared`.
from src.routers import campaigns as campaigns_router  # noqa: E402
from src.routers import categories as categories_router  # noqa: E402
from src.routers import contacts as contacts_router  # noqa: E402
from src.routers import inbox as inbox_router  # noqa: E402
from src.routers import notifications as notifications_router  # noqa: E402
from src.routers import rules as rules_router  # noqa: E402
from src.routers import settings as settings_router  # noqa: E402
from src.routers import signature as signature_router  # noqa: E402

app.include_router(campaigns_router.router)
app.include_router(categories_router.router)
app.include_router(contacts_router.router)
app.include_router(inbox_router.router)
app.include_router(notifications_router.router)
app.include_router(rules_router.router)
app.include_router(settings_router.router)
app.include_router(signature_router.router)
_graph_runtime_lock = asyncio.Lock()
_graph_runtime_instance_id = current_agent_instance_id()


async def _invoke_graph(graph, input_value, config, reload_runtime_config: bool = True):
    """Reload graph globals only when switching agent instances."""
    global _graph_runtime_instance_id
    async with _graph_runtime_lock:
        instance_id = current_agent_instance_id()
        if reload_runtime_config and instance_id != _graph_runtime_instance_id:
            reload_config()
            _graph_runtime_instance_id = instance_id
        return await graph.ainvoke(input_value, config)


@app.middleware("http")
async def tenant_context_middleware(request: Request, call_next):
    if not _bypasses_gateway_secret(request.url.path) and not _gateway_secret_is_valid(request):
        return Response(
            content='{"detail":"gateway authentication required"}',
            status_code=401,
            media_type="application/json",
        )
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
    # The draft as currently shown/edited in the UI; used as the redraft
    # baseline so manual edits survive a retouche.
    draft: dict | None = None


class GmailWebhookInput(BaseModel):
    message: dict = {}
    subscription: str | None = None

class GmailConnectStartResponse(BaseModel):
    authorization_url: str
    agent_instance_id: str
    scopes: list[str]
    expires_in_seconds: int = 600


class ConnectTestResponse(BaseModel):
    ok: bool
    provider: str
    mailbox: str = ""
    error: str = ""


class GmailConnectCallbackResponse(BaseModel):
    status: str
    agent_instance_id: str
    user_id: str
    mailbox_identity: str | None = None



class MemoryInput(BaseModel):
    triage_preferences: str
    response_preferences: str


class StyleInput(BaseModel):
    writing_style: str


class RoleInput(BaseModel):
    role_key: str
    display_name: str
    emails: list[str]
    dept: str | None = None


class RunResponse(BaseModel):
    run_id: str
    status: str  # "pending_approval" | "completed" | "failed"
    classification: str | None = None
    error: str | None = None
    pending_action: list | None = None  # list of Agent Inbox request objects when paused
    category: str | None = None
    category_display_name: str | None = None
    priority: str | None = None
    template: str | None = None
    workflow_owner: str | None = None
    workflow_approver: str | None = None
    workflow_route_to: list[str] | None = None
    workflow_dept: str | None = None
    assignee: str | None = None
    action_type: str | None = None
    confidence: str | None = None
    review_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    sla_label: str | None = None
    due_at: str | None = None
    overdue: bool = False
    overdue_by_seconds: int = 0
    escalated_at: str | None = None
    escalation_target: str | None = None


def _derive_action_type(pending_action: list | None, classification: str | None) -> str:
    if not pending_action or len(pending_action) == 0:
        return "unknown"
    action = pending_action[0]
    action_request = action.get("action_request") or {} if isinstance(action, dict) else {}
    name = action_request.get("action", "")
    if name == "write_email":
        return "reply_draft"
    if name == "forward_email":
        return "notify" if classification == "notify" else "forward"
    if name == "notify_internal":
        return "notify"
    if name == "reply_all":
        return "reply_all"
    if name == "create_draft":
        return "draft"
    if name in ("trash_email", "apply_label", "archive_email"):
        return "organize"
    if "campaign" in name:
        return "campaign"
    logger.info(f"Unknown pending action tool name: {name}")
    return "unknown"


def _derive_confidence(record: dict) -> tuple[str, str]:
    """Heuristic review-confidence band + French reason, from persisted fields only.

    No LLM and no stored column — computed on read like action_type. It gives the
    reviewer a coarse hint plus a one-line "why is this in the queue".
    """
    category = (record.get("category") or "").strip()
    route_to = record.get("workflow_route_to") or []
    dept = record.get("workflow_dept")
    has_workflow = bool(
        (category and category != "uncategorized")
        or record.get("template")
        or record.get("workflow_owner")
        or route_to
    )
    classification = record.get("classification")
    action_type = record.get("action_type") or _derive_action_type(
        record.get("pending_action"), classification
    )
    if has_workflow:
        if action_type in ("forward", "notify") and (route_to or dept):
            target = dept or route_to[0]
            return "élevée", f"Règle de routage : {target}".strip()
        display = record.get("category_display_name") or category or "workflow"
        return "élevée", f"Workflow reconnu : {display}".strip()
    if classification == "respond":
        return "moyenne", "Brouillon rédigé par l'agent, aucune règle déterministe."
    if classification == "notify" or action_type == "unknown":
        return "faible", "Classé « à notifier » sans règle claire — à vérifier."
    return "moyenne", "Validation requise avant envoi."


def _thread_config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def _format(result: dict, run_id: str) -> RunResponse:
    """Turn a graph result into a response — paused on approval, or completed."""
    interrupts = result.get("__interrupt__")
    if interrupts:
        action_type = _derive_action_type(interrupts[0].value, result.get("classification_decision"))
        confidence, review_reason = _derive_confidence({
            "pending_action": interrupts[0].value,
            "classification": result.get("classification_decision"),
            "category": result.get("category"),
            "category_display_name": result.get("category_display_name"),
            "template": result.get("template"),
            "workflow_owner": result.get("workflow_owner"),
            "workflow_route_to": result.get("workflow_route_to") or [],
            "workflow_dept": result.get("workflow_dept"),
            "action_type": action_type,
        })
        return RunResponse(
            run_id=run_id,
            status="pending_approval",
            pending_action=interrupts[0].value,
            category=result.get("category"),
            category_display_name=result.get("category_display_name"),
            priority=result.get("priority"),
            template=result.get("template"),
            workflow_owner=result.get("workflow_owner"),
            workflow_approver=result.get("workflow_approver"),
            workflow_route_to=result.get("workflow_route_to") or [],
            workflow_dept=result.get("workflow_dept"),
            assignee=result.get("assignee"),
            action_type=action_type,
            confidence=confidence,
            review_reason=review_reason,
        )
    if result.get("email_send_failed"):
        return RunResponse(
            run_id=run_id,
            status="failed",
            classification=result.get("classification_decision", "unknown"),
            error=result.get("email_send_failed"),
            category=result.get("category"),
            category_display_name=result.get("category_display_name"),
            priority=result.get("priority"),
            template=result.get("template"),
            workflow_owner=result.get("workflow_owner"),
            workflow_approver=result.get("workflow_approver"),
            workflow_route_to=result.get("workflow_route_to") or [],
            workflow_dept=result.get("workflow_dept"),
            assignee=result.get("assignee"),
        )
    return RunResponse(
        run_id=run_id,
        status="completed",
        classification=result.get("classification_decision", "unknown"),
        category=result.get("category"),
        category_display_name=result.get("category_display_name"),
        priority=result.get("priority"),
        template=result.get("template"),
        workflow_owner=result.get("workflow_owner"),
        workflow_approver=result.get("workflow_approver"),
        workflow_route_to=result.get("workflow_route_to") or [],
    )


def _record_response(run: RunResponse, email_input: dict | None = None) -> None:
    record_input = {**(email_input or {})}
    optional_fields = {
        "category": run.category,
        "category_display_name": run.category_display_name,
        "priority": run.priority,
        "template": run.template,
        "workflow_owner": run.workflow_owner,
        "workflow_approver": run.workflow_approver,
        "workflow_route_to": run.workflow_route_to,
        "workflow_dept": run.workflow_dept,
    }
    record_input["error"] = run.error
    for key, value in optional_fields.items():
        if value is not None:
            record_input[key] = value
    upsert_run(
        run.run_id,
        run.status,
        email_input=record_input,
        classification=run.classification,
        pending_action=run.pending_action,
        user_id=current_user_id(),
        agent_instance_id=current_agent_instance_id(),
    )


def _record_decision_metadata(run: RunResponse, record: dict, decision: str) -> None:
    upsert_run(
        run.run_id,
        run.status,
        email_input=_record_email_input(record),
        classification=run.classification,
        pending_action=run.pending_action,
        user_id=current_user_id(),
        agent_instance_id=current_agent_instance_id(),
        decision=decision,
        decision_at=_now_iso(),
    )


def _annotate_run_record(
    record: dict,
    categories_cfg: CategoriesConfig | None = None,
    escalation_state: dict | None = None,
) -> dict:
    enriched = dict(record)
    enriched["action_type"] = _derive_action_type(record.get("pending_action"), record.get("classification"))
    confidence, review_reason = _derive_confidence(enriched)
    enriched["confidence"] = confidence
    enriched["review_reason"] = review_reason
    if record.get("status") == "pending_approval":
        categories_cfg = categories_cfg or load_categories(agent_instance_id=current_agent_instance_id())
        escalation_state = escalation_state or load_escalation_state()
        enriched.update(workflow_sla_snapshot(record, categories_cfg, escalation_state=escalation_state))
    else:
        enriched.update({
            "sla_label": None,
            "due_at": None,
            "overdue": False,
            "overdue_by_seconds": 0,
            "escalated_at": None,
            "escalation_target": None,
        })
    return enriched


def _run_response_from_record(record: dict) -> RunResponse:
    record = _annotate_run_record(record)
    return RunResponse(
        run_id=record["run_id"],
        status=record.get("status") or "completed",
        classification=record.get("classification"),
        pending_action=record.get("pending_action"),
        category=record.get("category"),
        category_display_name=record.get("category_display_name"),
        priority=record.get("priority"),
        template=record.get("template"),
        workflow_owner=record.get("workflow_owner"),
        workflow_approver=record.get("workflow_approver"),
        workflow_route_to=record.get("workflow_route_to") or [],
        workflow_dept=record.get("workflow_dept"),
        assignee=record.get("assignee"),
        action_type=record.get("action_type"),
        confidence=record.get("confidence"),
        review_reason=record.get("review_reason"),
        created_at=record.get("created_at"),
        updated_at=record.get("updated_at"),
        sla_label=record.get("sla_label"),
        due_at=record.get("due_at"),
        overdue=bool(record.get("overdue")),
        overdue_by_seconds=int(record.get("overdue_by_seconds") or 0),
        escalated_at=record.get("escalated_at"),
        escalation_target=record.get("escalation_target"),
        error=record.get("error"),
    )


def _draft_content_from_response(response: RunResponse) -> str:
    pending = response.pending_action or []
    if not pending:
        return ""
    first = pending[0] if isinstance(pending[0], dict) else {}
    request = first.get("action_request") or {}
    if request.get("action") != "write_email":
        return ""
    args = request.get("args") or {}
    return str(args.get("content") or "")


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream_run_response(response: RunResponse):
    draft_content = _draft_content_from_response(response)
    if settings.llm_streaming_enabled and draft_content:
        emitted = ""
        chunk_size = 96
        for index in range(0, len(draft_content), chunk_size):
            delta = draft_content[index:index + chunk_size]
            emitted += delta
            yield _sse_event(
                "draft",
                {
                    "run_id": response.run_id,
                    "delta": delta,
                    "content": emitted,
                },
            )
            await asyncio.sleep(0)
    yield _sse_event("result", response.model_dump())
    yield _sse_event("end", {"run_id": response.run_id, "status": response.status})


def _record_decision_failure(run_id: str, action: str, exc: Exception, record: dict | None) -> None:
    """File an API-path failure in the DLQ.

    Best effort by design: a DLQ write that raises must never turn a failed
    action into a second, different failure.
    """
    try:
        record_dead_letter({
            "message_id": (record or {}).get("email_id") or run_id,
            "reason": f"decision_failed:{action}",
            "error": f"{type(exc).__name__}: {exc}",
            "payload": {"run_id": run_id, "action": action},
        })
    except Exception as dlq_exc:  # pragma: no cover - defensive
        logger.warning(f"api: could not record the DLQ entry for run {run_id}: {dlq_exc}")


def _pending_response_after_decision_error(run_id: str, exc: Exception, action: str) -> RunResponse | None:
    record = get_run_record(
        run_id,
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
    )
    # The dead-letter queue only ever heard from the poller, so a failure on this
    # path — an approval, a rejection, a redraft — left no trace anywhere the
    # operator looks. The failure page said "no DLQ entries" while the action had
    # visibly just failed in front of them.
    _record_decision_failure(run_id, action, exc, record)
    if not record or record.get("status") != "pending_approval":
        return None
    logger.warning(f"api: {action} failed for run {run_id}; keeping pending approval: {exc}")
    # The redraft give-up carries a user-facing French message; show it as-is
    # instead of wrapping it in the technical English envelope.
    if isinstance(exc, graph_module.RedraftGiveUpError):
        error_text = str(exc)
    else:
        error_text = f"Could not complete {action}; draft is still pending. {type(exc).__name__}: {exc}"
    return RunResponse(
        run_id=run_id,
        status="pending_approval",
        classification=record.get("classification"),
        pending_action=record.get("pending_action"),
        category=record.get("category"),
        category_display_name=record.get("category_display_name"),
        priority=record.get("priority"),
        template=record.get("template"),
        workflow_owner=record.get("workflow_owner"),
        workflow_approver=record.get("workflow_approver"),
        workflow_route_to=record.get("workflow_route_to") or [],
        error=error_text,
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
        "category": record.get("category"),
        "category_display_name": record.get("category_display_name"),
        "priority": record.get("priority"),
        "template": record.get("template"),
        "workflow_owner": record.get("workflow_owner"),
        "workflow_approver": record.get("workflow_approver"),
        "workflow_route_to": record.get("workflow_route_to") or [],
        "workflow_dept": record.get("workflow_dept"),
        "error": record.get("error"),
    }


def _complete_pending_rejection(run_id: str) -> RunResponse | None:
    record = get_run_record(
        run_id,
        user_id=None,
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
    _record_decision_metadata(response, record, "rejected")
    return response


def _execute_pending_action(run_id: str, args_override: dict | None = None) -> RunResponse | None:
    record = get_run_record(
        run_id,
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
    )
    if not record or record.get("status") != "pending_approval":
        return None
    # No request object available here to check dept, but this is an internal func 
    # called by approve/reject/respond endpoints which do check it.
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
    _record_decision_metadata(response, record, "approved")
    return response


ORPHANED_RUN_DETAIL = (
    "This run's conversation state is gone, so it can no longer be approved, "
    "edited or resumed. It has been marked as expired."
)


def _mark_run_orphaned(record: dict) -> None:
    """Registry says pending, checkpointer has nothing — retire the row.

    The two stores can drift (a storage-backend switch, a pruned checkpoint DB,
    a restore from an older dump). Left alone, the run keeps showing up in the
    approval queue as a card whose every button 404s, forever. Marking it here
    means the person sees one honest "expirée" state instead.
    """
    try:
        upsert_run(
            record["run_id"],
            "orphaned",
            email_input=_record_email_input(record),
            classification=record.get("classification"),
            pending_action=None,
            user_id=record.get("user_id"),
            agent_instance_id=record.get("agent_instance_id") or current_agent_instance_id(),
            created_at=record.get("created_at"),
        )
    except Exception as exc:  # pragma: no cover - defensive, never break the request
        logger.warning("run %s could not be marked orphaned: %s", record.get("run_id"), exc)


async def _run_has_state(graph, run_id: str) -> bool:
    state = await graph.aget_state(_thread_config(run_id))
    return bool(state.values)


async def _require_run(graph, run_id: str) -> dict:
    record = get_run_record(
        run_id,
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    config = _thread_config(run_id)
    if not await _run_has_state(graph, run_id):
        if record.get("status") in ACTIVE_RUN_STATUSES:
            _mark_run_orphaned(record)
        raise HTTPException(status_code=410, detail=ORPHANED_RUN_DETAIL)
    return config


_reconciled_instances: set[str] = set()


async def _ensure_orphans_reconciled(graph) -> None:
    """Reconcile once per instance per process, on the first queue read.

    Doing this at startup instead would have to guess the instance list; the
    first listing request already carries the tenant context we need.
    """
    instance_id = current_agent_instance_id()
    if instance_id in _reconciled_instances:
        return
    _reconciled_instances.add(instance_id)
    try:
        retired = await reconcile_orphaned_runs(graph)
    except Exception as exc:  # pragma: no cover - never fail a listing over this
        logger.warning("orphan reconciliation failed for %s: %s", instance_id, exc)
        return
    if retired:
        logger.warning("marked %s run(s) orphaned on %s: no checkpoint state", retired, instance_id)


async def reconcile_orphaned_runs(graph, limit: int = 500) -> int:
    """Retire queued runs whose checkpoint no longer exists. Runs at startup so
    the approval queue never opens on cards that cannot be acted on."""
    orphaned = 0
    for status in ACTIVE_RUN_STATUSES:
        for record in list_runs(
            status=status,
            user_id=None,
            agent_instance_id=current_agent_instance_id(),
            limit=limit,
        ):
            run_id = record.get("run_id")
            if not run_id:
                continue
            try:
                has_state = await _run_has_state(graph, run_id)
            except Exception:  # pragma: no cover - a probe failure is not proof of absence
                continue
            if not has_state:
                _mark_run_orphaned(record)
                orphaned += 1
    return orphaned


def _require_pending(run_id: str) -> None:
    """Guard against a second concurrent decision resuming an already-decided run.

    Two racing approve/reject/respond calls on the same run_id both pass
    `_require_run` (the thread still has state either way); without this check
    the second would resume a graph that already finished, an undefined
    operation for LangGraph. A fresh registry read here is the cheapest correct
    fence — no new lock primitive needed for the single-replica deployment.
    """
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    if record is not None and record.get("status") != "pending_approval":
        raise HTTPException(status_code=409, detail="This run already received a decision.")


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
        "category": values.get("category"),
        "category_display_name": values.get("category_display_name"),
        "priority": values.get("priority"),
        "template": values.get("template"),
        "workflow_owner": values.get("workflow_owner"),
        "workflow_approver": values.get("workflow_approver"),
        "workflow_route_to": values.get("workflow_route_to") or [],
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


def _is_stale_history_error(exc: Exception) -> bool:
    """Whether the stored sync cursor is too old to replay.

    Resolved per call rather than at import: the answer is provider-specific
    (Gmail purges history after ~a week, Graph answers 410 resyncRequired), and
    building a provider at import time would read instance config before any
    tenant context exists.
    """
    return get_provider().is_stale_cursor_error(exc)


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
        if not history_id_is_newer(pushed_history_id, baseline):
            return {"accepted": True, "history_id": pushed_history_id, "outcomes": [], "synced": False}
        try:
            outcomes = await poll_history(request.app.state.graph, baseline)
        except Exception as exc:
            # Stale baseline (history older than ~1 week is purged by Gmail). Reset
            # forward and ack so Pub/Sub stops retrying an unrecoverable window.
            if _is_stale_history_error(exc):
                set_last_history_id(pushed_history_id)
            record_sync_failure(str(exc))
            return {"accepted": True, "history_id": pushed_history_id, "outcomes": [], "synced": False}
        set_last_history_id(pushed_history_id)
        record_sync_success("webhook")

    return {"accepted": True, "history_id": pushed_history_id, "outcomes": outcomes}


@app.get("/health")
async def health() -> dict:
    components = await aggregate_health()
    return {"status": "ok", "storage_backend": settings.storage_backend, **components}


@app.get("/manifest")
async def manifest() -> dict:
    """Agent self-description, read by the gateway's agent registry.

    Unauthenticated and tenant-free on purpose: it describes the agent type, so
    there is nothing here that belongs to a user. See `src/manifest.py`.
    """
    return build_manifest()



@app.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request) -> PlainTextResponse:
    _require_instance_role(request, "viewer")
    return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4")


@app.get("/dlq")
async def dlq_list(request: Request, status: str | None = Query(default=None), limit: int = Query(default=100, ge=1, le=500)) -> dict:
    _require_instance_role(request, "owner")
    return {
        "entries": list_dead_letters(status=status, limit=limit, agent_instance_id=current_agent_instance_id()),
        "limit": limit,
    }


async def _reprocess_dead_letter(entry: dict, graph) -> dict:
    payload = dict(entry.get("payload") or {})
    run_id = str(uuid.uuid4())
    result = await _invoke_graph(
        graph,
        {"email_input": {**payload, "agent_instance_id": current_agent_instance_id()}},
        {"configurable": {"thread_id": run_id}},
    )
    status = "pending_approval" if result.get("__interrupt__") else ("failed" if result.get("email_send_failed") else ("notify" if result.get("classification_decision") == "notify" else "completed"))
    upsert_run(
        run_id,
        status,
        email_input=payload,
        classification=result.get("classification_decision"),
        pending_action=result["__interrupt__"][0].value if result.get("__interrupt__") else None,
        agent_instance_id=current_agent_instance_id(),
    )
    claim_dead_letter(entry["entry_id"], "requeued", "resolved", agent_instance_id=current_agent_instance_id())
    return {"entry_id": entry["entry_id"], "status": "requeued", "run_id": run_id}


@app.post("/dlq/{entry_id}/requeue")
async def dlq_requeue(request: Request, entry_id: str) -> dict:
    _require_instance_role(request, "owner")
    entry = get_dead_letter(entry_id, agent_instance_id=current_agent_instance_id())
    if entry is None:
        raise HTTPException(status_code=404, detail="DLQ entry not found")
    if entry.get("reason") == "mailbox_sync_failure":
        raise HTTPException(
            status_code=400,
            detail="A mailbox connection failure has no email to replay — reconnect the mailbox instead.",
        )
    claimed = claim_dead_letter(
        entry_id,
        "dead_letter",
        "requeued",
        agent_instance_id=current_agent_instance_id(),
        requeue_token=str(uuid.uuid4()),
    )
    if claimed is None:
        return {"entry_id": entry_id, "status": entry.get("status")}
    return await _reprocess_dead_letter(claimed, request.app.state.graph)



@app.get("/instance-setup")
async def get_instance_setup(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    return {"agent_instance_id": current_agent_instance_id(), **get_setup()}


@app.post("/instance-setup/start")
async def start_instance_setup(request: Request, background_tasks: BackgroundTasks) -> dict:
    _require_instance_role(request, "owner")
    existing = get_setup()
    if existing["status"] not in ("not_started", "ready", "failed"):
        raise HTTPException(status_code=409, detail="Setup is already running for this instance")
    result = await asyncio.to_thread(start_setup, current_user_id(), current_agent_instance_id())
    if not settings.job_queue_enabled:
        background_tasks.add_task(
            run_pipeline_inline, current_user_id(), current_agent_instance_id(), store=app.state.store
        )
    return {"agent_instance_id": current_agent_instance_id(), **result}


@app.post("/instance-setup/retry")
async def retry_instance_setup(request: Request, background_tasks: BackgroundTasks) -> dict:
    _require_instance_role(request, "owner")
    result = await asyncio.to_thread(
        start_setup, current_user_id(), current_agent_instance_id(), force=True
    )
    if not settings.job_queue_enabled:
        background_tasks.add_task(
            run_pipeline_inline, current_user_id(), current_agent_instance_id(), store=app.state.store
        )
    return {"agent_instance_id": current_agent_instance_id(), **result}


@app.post("/instance-setup/steps/{step_key}/retry")
async def retry_instance_setup_step(step_key: str, request: Request, background_tasks: BackgroundTasks) -> dict:
    _require_instance_role(request, "owner")
    from src.instance_setup import SETUP_STEPS, retry_step

    if step_key not in SETUP_STEPS:
        raise HTTPException(status_code=422, detail=f"Unknown setup step: {step_key}")
    result = await asyncio.to_thread(
        retry_step, step_key, current_user_id(), current_agent_instance_id()
    )
    if not settings.job_queue_enabled:
        background_tasks.add_task(
            run_pipeline_inline, current_user_id(), current_agent_instance_id(), store=app.state.store
        )
    return {"agent_instance_id": current_agent_instance_id(), **result}


class SetupCategoryInput(BaseModel):
    """One category as the owner describes it during onboarding."""

    name: str
    description: str | None = None
    policy: str = "auto_draft"
    priority: str = "normal"
    keywords: list[str] = Field(default_factory=list)


class SetupCategoriesInput(BaseModel):
    categories: list[SetupCategoryInput]


@app.post("/instance-setup/categories")
async def seed_instance_categories(request: Request, body: SetupCategoriesInput) -> dict:
    """Record the owner's own categories before the mailbox is read.

    Onboarding used to invent a default set (clients / externe / interne) and
    only then look at the mail, so the first classification every owner saw was
    somebody else's taxonomy. Collecting the real one first means the very first
    pass over the mailbox files messages into categories the owner recognises —
    "banque", "fournisseurs", whatever the business actually runs on.

    Names are the workflow keys, so they are slugged; the label the owner typed
    is kept as the display name. A category with no keywords still matches
    nothing on its own — it becomes a target for manual filing on the triage
    screen, which is the point of asking before the fetch rather than after.
    """
    _require_instance_role(request, "owner")
    import re as _re

    from src.automation import RuleWhen

    seen: set[str] = set()
    categories: list[Category] = []
    for entry in body.categories:
        label = entry.name.strip()
        if not label:
            continue
        slug = _re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        keywords = [k.strip() for k in entry.keywords if k.strip()]
        categories.append(
            Category(
                name=slug,
                display_name=label,
                description=entry.description or None,
                enabled=True,
                priority=entry.priority,
                policy=entry.policy,
                when=RuleWhen(subject_contains=keywords, body_contains=keywords),
            )
        )

    if not categories:
        raise HTTPException(status_code=422, detail="At least one category is required")

    _categories_yaml, cfg = _current_categories()
    cfg.enabled = True
    cfg.categories = categories
    payload = dump_categories(cfg)
    write_instance_text("categories", payload, DEFAULT_CATEGORIES_PATH)
    return {
        "agent_instance_id": current_agent_instance_id(),
        "categories_yaml": payload,
        "parsed": cfg.model_dump(),
    }


@app.post("/instance-setup/skip")
async def skip_instance_setup(request: Request) -> dict:
    _require_instance_role(request, "owner")
    from src.instance_setup import force_ready

    result = await asyncio.to_thread(force_ready, current_user_id(), current_agent_instance_id())
    return {"agent_instance_id": current_agent_instance_id(), **result}


@app.post("/gdpr/erase/dry-run")
async def gdpr_erase_dry_run(request: Request, body: ErasureRequest) -> dict:
    _require_instance_role(request, "owner")
    return await asyncio.to_thread(
        preview_erasure, body.email, body.agent_instance_id, body.revoke_owner_token
    )


@app.post("/gdpr/erase")
async def gdpr_erase(request: Request, body: ErasureRequest) -> dict:
    _require_instance_role(request, "owner")
    return await asyncio.to_thread(
        erase_subject, body.email, body.agent_instance_id, body.revoke_owner_token
    )


@app.get("/agent-instances/{instance_id}/connect/gmail/start", response_model=GmailConnectStartResponse)
async def gmail_connect_start(instance_id: str, request: Request) -> GmailConnectStartResponse:
    user_id = _request_user_id(request) or current_user_id()
    mailbox_identity = request.query_params.get("mailbox_identity") or ""
    try:
        state = build_gmail_oauth_state(user_id, instance_id, mailbox_identity=mailbox_identity)
        authorization_url = build_gmail_authorization_url(state)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return GmailConnectStartResponse(
        authorization_url=authorization_url,
        agent_instance_id=instance_id,
        scopes=GMAIL_SCOPES,
    )


@app.get(
    "/agent-instances/{instance_id}/connect/outlook/start",
    response_model=GmailConnectStartResponse,
)
async def outlook_connect_start(instance_id: str, request: Request) -> GmailConnectStartResponse:
    """Begin the Microsoft consent flow — the Outlook twin of the Gmail start."""
    user_id = _request_user_id(request) or current_user_id()
    mailbox_identity = request.query_params.get("mailbox_identity") or ""
    try:
        state = build_outlook_oauth_state(user_id, instance_id, mailbox_identity=mailbox_identity)
        authorization_url = build_outlook_authorization_url(state)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return GmailConnectStartResponse(
        authorization_url=authorization_url,
        agent_instance_id=instance_id,
        scopes=OUTLOOK_SCOPES,
    )


@app.get("/connect/outlook/callback")
async def outlook_connect_callback(
    background_tasks: BackgroundTasks, code: str | None = None, state: str | None = None
) -> RedirectResponse:
    if not code or not state:
        return _oauth_callback_redirect("outlook", None, "error", "Missing Outlook OAuth code or state.")
    payload: dict | None = None
    try:
        payload = validate_outlook_oauth_state(state)
        exchange_outlook_oauth_code(code, payload, state=state)
        with user_context(payload["user_id"]):
            with agent_instance_context(payload["agent_instance_id"]):
                # Record the provider before anything reads the mailbox: every
                # later call resolves through get_provider(), which would pick
                # Gmail by default and look for a token that does not exist.
                set_mail_provider("outlook", payload["agent_instance_id"])
                record_sync_success("oauth", payload["user_id"], payload["agent_instance_id"])
        if settings.setup_enabled:
            _start_setup_after_connect(background_tasks, payload["user_id"], payload["agent_instance_id"])
    except (ValueError, RuntimeError) as exc:
        logger.warning(f"api: outlook oauth callback rejected: {exc}")
        instance_id = payload["agent_instance_id"] if payload else None
        return _oauth_callback_redirect("outlook", instance_id, "error", str(exc))
    except Exception as exc:
        logger.warning(f"api: outlook oauth callback failed: {exc!r}\n{traceback.format_exc()}")
        instance_id = payload["agent_instance_id"] if payload else None
        return _oauth_callback_redirect(
            "outlook",
            instance_id,
            "error",
            f"Could not finish connecting to Microsoft ({type(exc).__name__}: {exc}). Try again.",
        )
    return _oauth_callback_redirect("outlook", payload["agent_instance_id"], "connected")


@app.post("/disconnect/outlook", status_code=200)
async def disconnect_outlook() -> dict:
    """Delete the stored Outlook token for the current agent instance.

    Microsoft has no per-token revoke endpoint, so this removes our copy; the
    directory-side grant survives until the user revokes it in their account.
    """
    user_id = current_user_id()
    agent_instance_id = current_agent_instance_id()
    removed = revoke_outlook_token(user_id, agent_instance_id)
    uid, iid = _sync_resolve(user_id, agent_instance_id)
    patch = {"connection_status": "disconnected", "sync_mode": "idle"}
    if _selected_run_registry_backend() == "postgres":
        _sync_pg_update(uid, iid, patch)
    else:
        _sync_json_update(uid, iid, patch)
    return {"disconnected": removed, "user_id": user_id, "agent_instance_id": agent_instance_id}


@app.post("/connect/test", response_model=ConnectTestResponse)
async def connect_test() -> ConnectTestResponse:
    """Round-trip the configured mailbox and report what came back.

    Answers "is this mailbox actually reachable right now" without waiting for
    the next poll cycle. The result is recorded through the normal sync status
    path so the failure reason shows up wherever connection state is displayed.
    """
    provider = get_provider()
    result = await asyncio.to_thread(provider.probe)
    if result.get("ok"):
        record_sync_success("probe")
    else:
        record_sync_failure(result.get("error") or "mailbox unreachable")
    return ConnectTestResponse(
        ok=bool(result.get("ok")),
        provider=provider.name,
        mailbox=result.get("mailbox") or "",
        error=result.get("error") or "",
    )


def _oauth_callback_redirect(
    provider: str, agent_instance_id: str | None, status: str, message: str = ""
) -> RedirectResponse:
    """Send OAuth completion back into the app.

    The frontend opens the provider in a named popup and keeps the main setup
    page visible. This redirect may therefore land inside the popup; the main
    window learns completion through polling, and the query parameters remain
    useful if the callback ever lands in the main workspace window.

    The `gmail=` query key is kept for both providers: the frontend already
    keys off it, and renaming it would break in-flight popups on deploy.
    """
    target = f"/oauth/{provider}/callback"
    query = f"gmail={status}"
    if agent_instance_id:
        query += f"&agent_instance_id={quote(agent_instance_id)}"
    if message:
        query += f"&message={quote(message)}"
    return RedirectResponse(f"{settings.app_base_url}{target}?{query}", status_code=303)


def _gmail_callback_redirect(agent_instance_id: str | None, status: str, message: str = "") -> RedirectResponse:
    return _oauth_callback_redirect("gmail", agent_instance_id, status, message)


def _start_setup_after_connect(background_tasks: BackgroundTasks, user_id: str, agent_instance_id: str) -> None:
    """Kick off onboarding right after a successful Gmail connect.

    A setup failure here must never break the OAuth success page — mirrors the
    existing broad-except discipline already used around this callback."""
    try:
        with user_context(user_id):
            with agent_instance_context(agent_instance_id):
                start_setup(user_id, agent_instance_id)
        if not settings.job_queue_enabled:
            background_tasks.add_task(
                run_pipeline_inline, user_id, agent_instance_id, store=app.state.store
            )
    except Exception as exc:
        logger.warning(f"api: failed to start onboarding for {user_id}/{agent_instance_id}: {exc}")


@app.get("/connect/gmail/callback")
async def gmail_connect_callback(
    background_tasks: BackgroundTasks, code: str | None = None, state: str | None = None
) -> RedirectResponse:
    if not code or not state:
        return _gmail_callback_redirect(None, "error", "Missing Gmail OAuth code or state.")
    payload: dict | None = None
    try:
        payload = validate_gmail_oauth_state(state)
        exchange_gmail_oauth_code(code, payload)
        with user_context(payload["user_id"]):
            with agent_instance_context(payload["agent_instance_id"]):
                set_mail_provider("gmail", payload["agent_instance_id"])
                record_sync_success("oauth", payload["user_id"], payload["agent_instance_id"])
        if settings.setup_enabled:
            _start_setup_after_connect(background_tasks, payload["user_id"], payload["agent_instance_id"])
    except (ValueError, RuntimeError) as exc:
        logger.warning(f"api: gmail oauth callback rejected: {exc}")
        instance_id = payload["agent_instance_id"] if payload else None
        return _gmail_callback_redirect(instance_id, "error", str(exc))
    except Exception as exc:
        # Token exchange reaches out to Google; a transient network failure (or a
        # stale/replayed single-use code) must not surface as a raw 500.
        logger.warning(f"api: gmail oauth callback failed: {exc!r}\n{traceback.format_exc()}")
        instance_id = payload["agent_instance_id"] if payload else None
        return _gmail_callback_redirect(
            instance_id,
            "error",
            f"Could not finish connecting to Google ({type(exc).__name__}: {exc}). Try again.",
        )
    return _gmail_callback_redirect(payload["agent_instance_id"], "connected")


# Clustering by sender domain only says something when the domain belongs to an
# organisation. A consumer mailbox provider groups unrelated people, and accepting
# it would create a `sender_domain` rule that swallows most personal mail.
@app.get("/roles")
async def get_roles(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    roles = list_roles()
    return {
        "agent_instance_id": current_agent_instance_id(),
        "roles": [_serialize_role(role) for role in roles],
        "storage": "roles-directory",
    }


@app.post("/roles", status_code=201)
async def create_role_entry(request: Request, body: RoleInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        role = create_role(body.role_key, body.display_name, body.emails, body.dept)
    except RoleConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "role": _serialize_role(role),
        "storage": "roles-directory",
    }


@app.put("/roles/{role_key}")
async def update_role_entry(role_key: str, request: Request, body: RoleInput) -> dict:
    _require_instance_role(request, "owner")
    if normalize_role_key(body.role_key) != normalize_role_key(role_key):
        raise HTTPException(status_code=400, detail="role_key in body must match the path")
    try:
        role = update_role(role_key, body.display_name, body.emails, body.dept)
    except RoleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "role": _serialize_role(role),
        "storage": "roles-directory",
    }


@app.delete("/roles/{role_key}")
async def delete_role_entry(role_key: str, request: Request) -> dict:
    _require_instance_role(request, "owner")
    try:
        delete_role(role_key)
    except RoleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "deleted": normalize_role_key(role_key),
        "storage": "roles-directory",
    }


@app.get("/templates")
async def get_templates() -> dict:
    _raw, cfg = _current_categories()
    return {
        "agent_instance_id": current_agent_instance_id(),
        "templates": [template.model_dump() for template in cfg.templates],
        "storage": "instance-config",
    }


# ---------------------------------------------------------------------------
# Outbound broadcast campaigns (segment-targeted + approval-gated).
# ---------------------------------------------------------------------------


@app.get("/config")
async def get_agent_config() -> dict:
    return load_config().model_dump()


@app.put("/config")
async def update_agent_config(body: dict) -> dict:
    cfg = AgentConfig(**body)
    write_instance_text(
        "config", yaml.safe_dump(cfg.model_dump(), sort_keys=False), DEFAULT_CONFIG_PATH
    )
    reload_config()  # persona/triage edits take effect without a restart (this process)
    return cfg.model_dump()


@app.get("/persona")
async def get_persona() -> dict:
    persona = load_persona()
    return {
        "agent_instance_id": current_agent_instance_id(),
        **persona.model_dump(),
        "compiled": compiled_preview(persona),
    }


@app.put("/persona")
async def update_persona(request: Request, body: Persona) -> dict:
    """Save the persona and compile it into the agent behavior texts.

    Compilation rewrites background / triage_instructions / response_preferences
    in the instance config (the graph consumes those unchanged); an empty
    persona is saved but leaves the config untouched.
    """
    _require_instance_role(request, "owner")
    save_persona(body)
    compiled = compiled_preview(body)
    if not body.is_empty():
        cfg = load_config()
        cfg.agent.background = compiled["background"]
        cfg.agent.triage_instructions = compiled["triage_instructions"]
        cfg.agent.response_preferences = compiled["response_preferences"]
        write_instance_text(
            "config", yaml.safe_dump(cfg.model_dump(), sort_keys=False, allow_unicode=True), DEFAULT_CONFIG_PATH
        )
        reload_config()
    return {
        "agent_instance_id": current_agent_instance_id(),
        **body.model_dump(),
        "compiled": compiled,
    }


@app.post("/persona/suggest")
async def suggest_persona_endpoint(request: Request) -> dict:
    """Analyse the mailbox and return persona prefill suggestions.

    Read-only: nothing is saved — the UI fills the form and the owner decides.
    """
    _require_instance_role(request, "owner")
    cfg = load_config()
    user_id = current_user_id()
    try:
        provider = get_provider()
        sent_samples = await asyncio.to_thread(provider.fetch_sent, cfg.style_learning.max_samples)
        received = await asyncio.to_thread(provider.list_inbox, 25)
    except Exception as exc:
        logger.warning(f"api: persona suggestion Gmail read unavailable for user {user_id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Gmail is unavailable. Check OAuth credentials and container network access.",
        ) from exc
    if not sent_samples and not received:
        raise HTTPException(status_code=422, detail="No usable mailbox samples found for persona suggestions")
    try:
        suggestion = await asyncio.to_thread(
            suggest_persona, sent_samples, received, graph_module.llm
        )
    except Exception as exc:
        logger.warning(f"api: persona suggestion analysis failed for user {user_id}: {exc}")
        raise HTTPException(status_code=503, detail="Persona analysis failed with the configured LLM") from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "suggestion": suggestion.model_dump(),
        "sent_sample_count": len(sent_samples),
        "received_sample_count": len(received),
    }


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


async def _memory_kind_entry(request: Request, kind: str) -> tuple[str, str]:
    """The stored preference text for a kind plus where it came from."""
    cfg = load_config()
    store = request.app.state.store
    item = await store.aget(namespace(kind), "user_preferences")
    if item:
        return preferences_text(item.value), preferences_origin(item.value) or ORIGIN_LEARNED
    defaults = {
        "triage_preferences": cfg.agent.triage_instructions,
        "response_preferences": cfg.agent.response_preferences,
        "writing_style": cfg.agent.writing_style_default,
    }
    return defaults.get(kind, ""), ORIGIN_DEFAULT


async def _memory_kind_text(request: Request, kind: str) -> str:
    text, _ = await _memory_kind_entry(request, kind)
    return text


@app.get("/memory/summary")
async def memory_summary(request: Request) -> dict:
    """Readable memory: one deletable French line per learned item, per kind."""
    summary: dict = {"agent_instance_id": current_agent_instance_id()}
    origins: dict = {}
    for kind in MEMORY_KINDS:
        text, origin = await _memory_kind_entry(request, kind)
        origins[kind] = origin
        summary[kind] = await asyncio.to_thread(
            summarize_kind, kind, text, graph_module.llm
        )
    summary["origins"] = origins
    return summary


@app.delete("/memory/item")
async def delete_memory_item(request: Request, kind: str, id: str) -> dict:
    _require_instance_role(request, "owner")
    if kind not in MEMORY_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {MEMORY_KINDS}")
    text = await _memory_kind_text(request, kind)
    updated = remove_item(kind, text, id)
    if updated is None:
        raise HTTPException(status_code=404, detail="memory item not found")
    store = request.app.state.store
    await store.aput(namespace(kind), "user_preferences", wrap_preferences(updated, ORIGIN_MANUAL))
    return {
        "agent_instance_id": current_agent_instance_id(),
        "kind": kind,
        "removed": id,
        "remaining": len(memory_items(kind, updated)),
    }


@app.put("/memory")
async def update_preferences(request: Request, body: MemoryInput) -> dict:
    # AsyncSqliteStore: must use the async API on the event loop (sync calls raise).
    store = request.app.state.store
    await store.aput(
        namespace("triage_preferences"),
        "user_preferences",
        wrap_preferences(body.triage_preferences, ORIGIN_MANUAL),
    )
    await store.aput(
        namespace("response_preferences"),
        "user_preferences",
        wrap_preferences(body.response_preferences, ORIGIN_MANUAL),
    )
    return {
        "triage_preferences": body.triage_preferences,
        "response_preferences": body.response_preferences,
        "origin": ORIGIN_MANUAL,
    }


@app.get("/style")
async def get_style(request: Request) -> dict:
    cfg = load_config()
    store = request.app.state.store
    item = await store.aget(namespace("writing_style"), "user_preferences")
    writing_style = preferences_text(item.value) if item else cfg.agent.writing_style_default
    return {
        "agent_instance_id": current_agent_instance_id(),
        "enabled": cfg.style_learning.enabled,
        "max_samples": cfg.style_learning.max_samples,
        "writing_style": writing_style,
        "source": "learned" if item else "default",
        # The store only holds the rendered text; the UI's structured panel needs
        # it parsed back, and the origin to say who wrote it.
        "profile": parse_style_text(writing_style).model_dump(),
        "origin": (preferences_origin(item.value) or ORIGIN_LEARNED) if item else ORIGIN_DEFAULT,
    }


@app.put("/style")
async def update_style(request: Request, body: StyleInput) -> dict:
    store = request.app.state.store
    await store.aput(
        namespace("writing_style"), "user_preferences", wrap_preferences(body.writing_style, ORIGIN_MANUAL)
    )
    return {
        "agent_instance_id": current_agent_instance_id(),
        "profile": parse_style_text(body.writing_style).model_dump(),
        "origin": ORIGIN_MANUAL,
        "writing_style": body.writing_style,
        "source": "manual",
    }


@app.post("/style/learn")
async def learn_style(request: Request) -> dict:
    cfg = load_config()
    if not cfg.style_learning.enabled:
        raise HTTPException(status_code=409, detail="Style learning is disabled for this agent instance")
    user_id = current_user_id()
    try:
        samples = await asyncio.to_thread(get_provider().fetch_sent, cfg.style_learning.max_samples)
    except Exception as exc:
        logger.warning(f"api: style learning Gmail read unavailable for user {user_id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Gmail sent mail is unavailable. Check OAuth credentials and container network access.",
        ) from exc
    if not samples:
        raise HTTPException(status_code=422, detail="No usable sent-mail samples found for style learning")
    try:
        profile = await asyncio.to_thread(analyze_style, samples, graph_module.llm)
    except Exception as exc:
        message = str(exc)
        logger.warning(f"api: style learning analysis failed for user {user_id}: {exc}")
        if "rate_limit" in message or "429" in message or "Rate limit" in message:
            raise HTTPException(
                status_code=429,
                detail="Style learning hit the LLM rate limit. Wait a few seconds and try again.",
            ) from exc
        raise HTTPException(status_code=503, detail="Style analysis failed with the configured LLM") from exc
    writing_style = build_style_text(profile)
    await request.app.state.store.aput(
        namespace("writing_style"),
        "user_preferences",
        wrap_preferences(writing_style, ORIGIN_LEARNED),
    )
    return {
        "agent_instance_id": current_agent_instance_id(),
        "sample_count": len(samples),
        "profile": profile.model_dump(),
        "origin": ORIGIN_LEARNED,
        "writing_style": writing_style,
    }


@app.get("/costs/summary")
async def cost_summary(period: str = Query(default="session")) -> dict:
    if period not in {"session", "day", "month"}:
        raise HTTPException(status_code=422, detail="period must be one of: session, day, month")
    return summarize_costs(
        period,
        user_id=current_user_id(),
        agent_instance_id=current_agent_instance_id(),
    )


@app.get("/costs")
async def costs(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    return {
        "costs": list_costs(
            user_id=current_user_id(),
            agent_instance_id=current_agent_instance_id(),
            limit=limit,
        ),
        "limit": limit,
    }


@app.get("/analytics")
async def analytics(request: Request, period: str = Query(default="week")) -> dict:
    if period not in {"day", "week", "month"}:
        raise HTTPException(status_code=422, detail="period must be one of: day, week, month")
    instance_role = (request.headers.get("x-agora-instance-role") or "").lower()
    dept_filter = None
    if instance_role not in {"owner", "admin"}:
        dept_filter = _request_user_dept(request)
    return summarize_analytics(
        period,
        user_id=current_user_id(),
        agent_instance_id=current_agent_instance_id(),
        workflow_dept=dept_filter,
    )


@app.get("/runs")
async def runs(
    request: Request,
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    q: str | None = Query(default=None),
    since: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    user_dept = _request_user_dept(request)
    await _ensure_orphans_reconciled(request.app.state.graph)
    # Fetch extra limit so we can filter post-db and check has_more, wait, list_runs in json/postgres needs to return all if we filter post-db.
    # To keep pagination working properly, we'll fetch an un-paginated chunk, filter it, and then paginate in python.
    all_runs = await asyncio.to_thread(
        list_runs,
        status=status,
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
        limit=5000,
    )
    if user_dept:
        all_runs = [
            r for r in all_runs
            if not r.get("workflow_dept") or _normalize_dept(r.get("workflow_dept")) == _normalize_dept(user_dept)
        ]
    if category:
        all_runs = [r for r in all_runs if str(r.get("category") or "") == category]
    if priority:
        all_runs = [r for r in all_runs if str(r.get("priority") or "normal") == priority]
    if q and q.strip():
        needle = q.strip().lower()
        all_runs = [
            r for r in all_runs
            if needle in str(r.get("author") or "").lower()
            or needle in str(r.get("subject") or "").lower()
        ]
    if since and since.strip():
        try:
            since_dt = datetime.fromisoformat(since.strip().replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            since_dt = since_dt.astimezone(timezone.utc)
        except ValueError:
            raise HTTPException(status_code=422, detail="since must be an ISO date or datetime")
        all_runs = [r for r in all_runs if _run_timestamp_at_or_after(r, since_dt)]

    categories_cfg = load_categories(agent_instance_id=current_agent_instance_id())
    escalation_state = load_escalation_state()
    page = [
        _annotate_run_record(item, categories_cfg=categories_cfg, escalation_state=escalation_state)
        for item in all_runs[offset:offset + limit + 1]
    ]
    has_more = len(page) > limit
    return {"runs": page[:limit], "limit": limit, "offset": offset, "has_more": has_more}


async def _events_generator(request: Request, agent_instance_id: str, user_dept: str | None):
    """Server-side change-detection loop over the run registry, streamed as SSE.

    The API and poller are separate processes sharing the same store (SQLite file
    or Postgres), so this is not a client-visible poll: the browser opens one
    long-lived connection and gets pushed a 'run_updated' event the moment this
    loop next notices a run changed, instead of re-polling /runs on a timer.
    Diffs full (run_id -> updated_at) snapshots rather than filtering by timestamp
    cursor — updated_at has only second precision, so a naive ">" cursor comparison
    could miss a second update landing within the same wall-clock second.
    """
    last_state: dict[str, str] = {}
    first_tick = True
    try:
        while True:
            if await request.is_disconnected():
                break
            all_runs = await asyncio.to_thread(
                list_runs,
                user_id=None,
                agent_instance_id=agent_instance_id,
                limit=5000,
            )
            if user_dept:
                all_runs = [
                    r for r in all_runs
                    if not r.get("workflow_dept")
                    or _normalize_dept(r.get("workflow_dept")) == _normalize_dept(user_dept)
                ]
            current_state = {r["run_id"]: r.get("updated_at") or "" for r in all_runs}

            if first_tick:
                # Baseline only — a fresh connection must not replay run history.
                last_state = current_state
                first_tick = False
            else:
                changed_ids = {
                    run_id for run_id, updated_at in current_state.items()
                    if last_state.get(run_id) != updated_at
                }
                if changed_ids:
                    categories_cfg = load_categories(agent_instance_id=agent_instance_id)
                    escalation_state = load_escalation_state()
                    for record in all_runs:
                        if record["run_id"] in changed_ids:
                            annotated = _annotate_run_record(
                                record, categories_cfg=categories_cfg, escalation_state=escalation_state
                            )
                            yield _sse_event("run_updated", annotated)
                    last_state = current_state
                else:
                    yield _sse_event(
                        "heartbeat", {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                    )
            await asyncio.sleep(settings.events_poll_interval_seconds)
    except asyncio.CancelledError:
        pass


@app.get("/events")
async def events_stream(request: Request) -> StreamingResponse:
    """Push run changes (new/updated pending approvals, completions) as SSE.

    Same visibility scope as GET /runs: current agent instance, department-filtered
    for non-owner/admin roles. Gateway gates this the same as GET /api/agent/runs
    (owner/viewer/admin).
    """
    agent_instance_id = current_agent_instance_id()
    user_dept = _request_user_dept(request)
    return StreamingResponse(
        _events_generator(request, agent_instance_id, user_dept),
        media_type="text/event-stream",
    )


@app.post("/sync")
async def sync_unread(request: Request, limit: int | None = Query(default=None, ge=1, le=100)) -> dict:
    """Process unread Gmail messages now so validation reflects fresh mail."""
    user_id = current_user_id()
    # Manual sync must respect the Gmail rate-limit cooldown: calling Gmail
    # during a served ban only extends it. Two signals: the in-process pause
    # (API-triggered 429s) and the shared sync_status written by the poller
    # container (its last failure being a fresh unresolved rate-limit error).
    pause_remaining = gmail_rate_limit_pause_remaining()
    if pause_remaining <= 0:
        status_snapshot = get_sync_status()
        err = (status_snapshot.get("last_error") or "").lower()
        if "ratelimitexceeded" in err or "rate limit" in err or "too many requests" in err:
            last_failure = status_snapshot.get("last_failure_at") or ""
            last_success = status_snapshot.get("last_success_at") or ""
            if last_failure and last_failure > last_success:
                try:
                    failed_at = datetime.fromisoformat(last_failure.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - failed_at).total_seconds()
                except ValueError:
                    age = 0.0
                if age < 600:
                    pause_remaining = 600 - age
    if pause_remaining > 0:
        raise HTTPException(
            status_code=429,
            detail=(
                "Limite Gmail atteinte côté Google. La synchronisation est en pause et "
                f"reprendra automatiquement dans environ {int(pause_remaining // 60) + 1} min."
            ),
        )
    provider = get_provider()
    reachable = await asyncio.to_thread(provider.probe)
    if not reachable.get("ok"):
        error = reachable.get("error") or "mailbox unreachable"
        logger.warning(f"api: gmail sync unavailable for user {user_id}: {error}")
        record_sync_failure(error)
        raise HTTPException(
            status_code=503,
            detail="Gmail sync is unavailable. Check OAuth credentials and container network access.",
        )

    try:
        effective_limit = limit or load_runtime_settings(current_agent_instance_id()).sync_limit
        outcomes = await poll_once(request.app.state.graph, provider=provider, max_results=effective_limit)
    except Exception as exc:
        logger.warning(f"api: gmail sync failed for user {user_id}: {exc}")
        record_sync_failure(str(exc))
        detail = public_sync_error_message(str(exc))
        status_code = 429 if "rate limit" in detail.lower() else 503
        raise HTTPException(status_code=status_code, detail=detail) from exc
    record_sync_success("manual")
    return {"outcomes": outcomes}


@app.get("/sync/status")
async def sync_status() -> dict:
    """Return the mailbox sync observability state for the current agent instance.

    Carries `provider` so callers — the web app and the gateway's mailbox
    overview — can render and act on the right connect flow without the gateway
    having to keep its own copy of that setting.
    """
    return {**get_sync_status(), "provider": get_mail_provider(current_agent_instance_id())}


@app.post("/sync/pause", status_code=200)
async def sync_pause() -> dict:
    """Pause automatic Gmail polling for the current agent instance."""
    set_sync_paused(True)
    return {"paused": True}


@app.post("/sync/resume", status_code=200)
async def sync_resume() -> dict:
    """Resume automatic Gmail polling for the current agent instance."""
    set_sync_paused(False)
    return {"paused": False}


@app.post("/disconnect/gmail", status_code=200)
async def disconnect_gmail() -> dict:
    """Revoke and delete the stored Gmail OAuth token for the current agent instance.

    This disconnects the instance from Gmail. A new OAuth flow is required to reconnect.
    The operation is irreversible — any pending runs that need Gmail access will fail
    until the instance is reconnected.
    """
    user_id = current_user_id()
    agent_instance_id = current_agent_instance_id()
    removed = revoke_gmail_token(user_id, agent_instance_id)
    uid, iid = _sync_resolve(user_id, agent_instance_id)
    patch = {"connection_status": "disconnected", "sync_mode": "idle"}
    if _selected_run_registry_backend() == "postgres":
        _sync_pg_update(uid, iid, patch)
    else:
        _sync_json_update(uid, iid, patch)
    return {"disconnected": removed, "user_id": user_id, "agent_instance_id": agent_instance_id}


@app.delete("/style", status_code=200)
async def delete_style(request: Request) -> dict:
    """Clear the learned writing style for the current agent instance."""
    store = request.app.state.store
    await store.adelete(namespace("writing_style"), "user_preferences")
    return {"cleared": True, "namespace": "writing_style"}


@app.delete("/memory", status_code=200)
async def delete_memory(request: Request) -> dict:
    """Clear all learned memory (triage preferences, response preferences, writing style)
    for the current agent instance. This cannot be undone; the agent will reseed defaults
    from config.yaml on the next run.
    """
    store = request.app.state.store
    for kind in ("triage_preferences", "response_preferences", "writing_style"):
        await store.adelete(namespace(kind), "user_preferences")
    return {"cleared": True, "namespaces": ["triage_preferences", "response_preferences", "writing_style"]}


# Cached Gmail inbox listings, keyed by (user, instance, limit).
#
# Listing the inbox is a network round trip to Google — a list call plus a
# metadata batch — and it was being paid on every visit to the Messages view.
# The mailbox itself changes slowly compared to how often the view is opened, so
# the listing is held briefly and served immediately; verdicts are re-attached
# from the local registry on every request, and any action that mutates the
# mailbox drops the entry so the next read is authoritative.
# How long an expired listing may still be shown while a fresh one is fetched.
# Past the TTL the entry is stale, not wrong: the messages are still the ones in
# the mailbox, only the "is there anything newer" answer has aged. Blocking the
# view on a live Gmail round trip to find out is what made opening Messages feel
# like the app had hung — and the poller invalidates this cache every cycle, so
# that round trip was landing on ordinary visits, not rare ones.
@app.get("/run/{run_id}", response_model=RunResponse)
async def get_run(request: Request, run_id: str) -> RunResponse:
    record = get_run_record(
        run_id, user_id=None, agent_instance_id=current_agent_instance_id()
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    _require_dept_access(request, record)
    return _run_response_from_record(record)


@app.get("/run/{run_id}/detail")
async def get_run_detail(request: Request, run_id: str) -> dict:
    """Read-only view of one run.

    Deliberately does not require graph state. Plenty of runs never have any:
    a message stopped by the junk gate is filed straight into the registry
    without ever reaching the graph, so demanding a checkpoint here answered
    "this run has expired" for runs that had simply never needed one. What the
    registry knows — sender, subject, verdict, why it was gated — is the whole
    point of the page, and it is always there.
    """
    graph = request.app.state.graph
    record = get_run_record(
        run_id, user_id=None, agent_instance_id=current_agent_instance_id()
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")

    state = await graph.aget_state(_thread_config(run_id))
    detail = _run_detail(state.values, run_id) if state.values else {"run_id": run_id, "messages": []}
    detail["has_graph_state"] = bool(state.values)
    detail["trace"] = list_traces(run_id=run_id, agent_instance_id=current_agent_instance_id(), limit=500)
    _require_dept_access(request, record)
    if record is not None:
        record = _annotate_run_record(record)
        detail.update({
            "status": record.get("status"),
            "classification": record.get("classification"),
            "category": record.get("category"),
            "category_display_name": record.get("category_display_name"),
            "priority": record.get("priority"),
            "template": record.get("template"),
            "workflow_owner": record.get("workflow_owner"),
            "workflow_approver": record.get("workflow_approver"),
            "workflow_route_to": record.get("workflow_route_to") or [],
            "sla_label": record.get("sla_label"),
            "due_at": record.get("due_at"),
            "overdue": record.get("overdue"),
            "overdue_by_seconds": record.get("overdue_by_seconds"),
            "escalated_at": record.get("escalated_at"),
            "escalation_target": record.get("escalation_target"),
            "subject": record.get("subject"),
            "author": record.get("author"),
            "junk_reason": record.get("junk_reason"),
            "sensitive_reason": record.get("sensitive_reason"),
            "decision": record.get("decision"),
        })
    return detail


@app.post("/run", response_model=RunResponse)
async def run(request: Request, email: EmailInput) -> RunResponse:
    graph = request.app.state.graph
    run_id = str(uuid.uuid4())
    # This endpoint creates synthetic/manual runs. Gmail identifiers are trusted
    # only when they come from gmail_to_email_input in the poller.
    submitted = email.model_dump()
    submitted.pop("email_id", None)
    submitted.pop("gmail_thread_id", None)
    submitted.pop("agent_instance_id", None)
    email_input = {**submitted, "agent_instance_id": current_agent_instance_id()}
    result = await _invoke_graph(
        graph,
        {"email_input": email_input}, _thread_config(run_id)
    )
    response = _format(result, run_id)
    _record_response(response, email_input)
    return response


@app.post("/run/stream")
async def run_stream(request: Request, email: EmailInput) -> StreamingResponse:
    graph = request.app.state.graph
    run_id = str(uuid.uuid4())
    submitted = email.model_dump()
    submitted.pop("email_id", None)
    submitted.pop("gmail_thread_id", None)
    submitted.pop("agent_instance_id", None)
    email_input = {**submitted, "agent_instance_id": current_agent_instance_id()}
    result = await _invoke_graph(
        graph,
        {"email_input": email_input}, _thread_config(run_id)
    )
    response = _format(result, run_id)
    _record_response(response, email_input)
    return StreamingResponse(_stream_run_response(response), media_type="text/event-stream")


async def _approve_run(graph, run_id: str, args) -> RunResponse:
    """Resume a paused run with an approve decision. Authorization is the caller's job."""
    config = await _require_run(graph, run_id)
    _require_pending(run_id)
    try:
        result = await _invoke_graph(
            graph,
            Command(resume={"type": "approve", "args": args}), config, reload_runtime_config=False
        )
    except Exception as exc:
        response = _execute_pending_action(run_id, args)
        if response is not None:
            logger.warning(f"api: approve graph resume failed for run {run_id}; used pending action fallback: {exc}")
            return response
        response = _pending_response_after_decision_error(run_id, exc, "approve")
        if response is not None:
            return response
        raise
    response = _format(result, run_id)
    _record_response(response)
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    if response.status == "completed" and record is not None:
        _record_decision_metadata(response, record, "approved")
    return response


async def _reject_run(graph, run_id: str) -> RunResponse:
    """Resolve a paused run with an ignore/reject decision. Authorization is the caller's job."""
    _require_pending(run_id)
    response = _complete_pending_rejection(run_id)
    if response is not None:
        return response
    # If a legacy/non-pending run is missing from the registry, fall back to the
    # old graph validation so callers still get a precise 404.
    await _require_run(graph, run_id)
    raise HTTPException(status_code=409, detail="Run is not pending approval")


@app.post("/run/{run_id}/attachments")
async def upload_run_attachment(
    request: Request, run_id: str, file: UploadFile = File(...)
) -> dict:
    """Stage a file a reviewer wants attached to this run's pending draft/send.

    The returned attachment_id is opaque to the model — it only ever reaches
    a tool through _REVIEWER_ATTACHMENTS_KEY in an approve/edit payload, the
    same trusted-context path _recipients uses. Files are deleted once the
    run resolves (src/run_registry.py, discard_run_attachments).
    """
    _require_instance_role(request, "approver")
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    _require_dept_access(request, record)
    _require_pending(run_id)
    data = await file.read()
    if len(data) > settings.max_attachment_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {settings.max_attachment_bytes} byte attachment limit.",
        )
    try:
        entry = await asyncio.to_thread(
            save_run_attachment, run_id, file.filename or "attachment", file.content_type, data
        )
    except AttachmentLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return entry


@app.get("/run/{run_id}/attachments")
async def list_run_attachments(request: Request, run_id: str) -> dict:
    _require_instance_role(request, "viewer")
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    _require_dept_access(request, record)
    return {"attachments": staged_run_attachments(run_id)}


@app.post("/run/{run_id}/approve", response_model=RunResponse)
async def approve(request: Request, run_id: str, approval: ApprovalInput) -> RunResponse:
    _require_instance_role(request, "approver")
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    _require_dept_access(request, record)
    return await _approve_run(request.app.state.graph, run_id, approval.args)


@app.post("/run/{run_id}/reject", response_model=RunResponse)
async def reject(request: Request, run_id: str) -> RunResponse:
    _require_instance_role(request, "approver")
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    _require_dept_access(request, record)
    return await _reject_run(request.app.state.graph, run_id)


class BulkDecisionInput(BaseModel):
    run_ids: list[str]
    decision: str  # "approve" | "reject"


@app.post("/runs/bulk")
async def bulk_decision(request: Request, body: BulkDecisionInput) -> dict:
    """Approve or reject several pending runs in one call.

    Each run is resumed through the same gated per-run path, so external sends
    stay individually authorized; a run the caller can't access (dept scope) or
    that isn't resolvable is skipped with an error entry, others still process.
    """
    _require_instance_role(request, "approver")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'")
    graph = request.app.state.graph
    results: list[dict] = []
    for run_id in body.run_ids:
        record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
        try:
            _require_dept_access(request, record)
        except HTTPException as exc:
            results.append({"run_id": run_id, "status": "denied", "error": str(exc.detail)})
            continue
        try:
            if body.decision == "approve":
                response = await _approve_run(graph, run_id, None)
            else:
                response = await _reject_run(graph, run_id)
            results.append({"run_id": run_id, "status": response.status, "error": response.error})
        except HTTPException as exc:
            results.append({"run_id": run_id, "status": "error", "error": str(exc.detail)})
        except Exception as exc:
            results.append({"run_id": run_id, "status": "error", "error": str(exc)})
    return {"results": results}


@app.post("/run/{run_id}/respond", response_model=RunResponse)
async def respond(request: Request, run_id: str, body: RespondInput) -> RunResponse:
    _require_instance_role(request, "approver")
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    _require_dept_access(request, record)
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    _require_pending(run_id)
    try:
        result = await _invoke_graph(
            graph,
            Command(resume=[{"type": "response", "args": body.feedback, "draft": body.draft}]), config, reload_runtime_config=False
        )
    except Exception as exc:
        response = _pending_response_after_decision_error(run_id, exc, "regenerate draft")
        if response is not None:
            return response
        raise
    response = _format(result, run_id)
    _record_response(response)
    return response


async def _respond_stream_events(graph, config: dict, run_id: str, feedback: str, draft: dict | None = None):
    yield _sse_event("status", {
        "run_id": run_id,
        "message": "L’agent rédige une nouvelle version du brouillon...",
    })
    try:
        result = await _invoke_graph(
            graph,
            Command(resume=[{"type": "response", "args": feedback, "draft": draft}]), config, reload_runtime_config=False
        )
        response = _format(result, run_id)
        _record_response(response)
    except Exception as exc:
        response = _pending_response_after_decision_error(run_id, exc, "regenerate draft")
        if response is None:
            yield _sse_event("error", {"run_id": run_id, "message": str(exc)})
            yield _sse_event("end", {"run_id": run_id, "status": "failed"})
            return
    async for chunk in _stream_run_response(response):
        yield chunk


@app.post("/run/{run_id}/respond/stream")
async def respond_stream(request: Request, run_id: str, body: RespondInput) -> StreamingResponse:
    _require_instance_role(request, "approver")
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    _require_dept_access(request, record)
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    _require_pending(run_id)
    return StreamingResponse(_respond_stream_events(graph, config, run_id, body.feedback, body.draft), media_type="text/event-stream")


@app.post("/run/{run_id}/summarize")
async def summarize_run(request: Request, run_id: str) -> dict:
    """TL;DR of the email thread behind this run — read-only, no state change."""
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    _require_dept_access(request, record)
    graph = request.app.state.graph
    config = await _require_run(graph, run_id)
    state = await graph.aget_state(config)
    thread = state.values.get("email_input", {}).get("email_thread", "")
    summary = await asyncio.to_thread(summarize_thread, thread, graph_module.llm)
    return {"run_id": run_id, "summary": summary}


class ToneInput(BaseModel):
    tone: str


@app.post("/run/{run_id}/tone")
async def tone_adjust(request: Request, run_id: str, body: ToneInput) -> dict:
    """Suggests a tone-adjusted rewrite of the pending draft — does not apply it.

    The caller re-submits the rewritten text as an edit through the normal
    approve args-override path, same as any other manual draft edit.
    """
    if body.tone not in TONES:
        raise HTTPException(status_code=400, detail=f"tone must be one of {TONES}")
    record = get_run_record(run_id, user_id=None, agent_instance_id=current_agent_instance_id())
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    _require_dept_access(request, record)
    pending = _pending_action(record)
    if pending is None:
        raise HTTPException(status_code=404, detail="No pending draft for this run")
    _, args = pending
    field = next((key for key in ("content", "body", "note") if args.get(key)), None)
    if field is None:
        raise HTTPException(status_code=400, detail="Pending action has no rewritable text field")
    rewritten = await asyncio.to_thread(adjust_tone, args[field], body.tone, graph_module.llm)
    return {"run_id": run_id, "field": field, "content": rewritten}
