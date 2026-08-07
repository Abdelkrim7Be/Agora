from __future__ import annotations

import asyncio
import base64
import contextlib
import html
import json
import logging
import os
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from email.utils import parseaddr
from urllib.parse import quote

import yaml
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from src.config import settings, validate_gmail_webhook_config, validate_model_redaction
from src.cost_tracker import list_costs, setup_cost_tracker, summarize as summarize_costs
from src.trace import list_traces, setup_trace_store
from src.dlq import claim_dead_letter, get_dead_letter, list_dead_letters, record_dead_letter, setup_dlq
from src.metrics import render_metrics
from src.manifest import build_manifest
from src.categories import (
    CategoriesConfig,
    Category,
    Contact as LegacyCategoryContact,
    DEFAULT_CATEGORIES_PATH,
    classify_category,
    dump_categories,
    load_categories,
)
from src.automation import (
    AutomationRule,
    DEFAULT_RULES_PATH,
    RulesConfig,
    apply_starter_rules,
    load_escalation_state,
    load_rules,
    starter_rule_catalogue,
    workflow_sla_snapshot,
)
from src.analytics import summarize as summarize_analytics
from src.config import AgentConfig, DEFAULT_CONFIG_PATH, SERVICE_ROOT, load_config
from src import graph as graph_module
from src.capabilities import current_email_id, current_gmail_thread_id, hitl_approved
from src.graph import overall_workflow, reload_config
from src.instance_config import read_instance_text, write_instance_text
from src.junk_config import JunkConfig, load_junk, save_junk, suggest_junk_senders
from src.poller import gmail_rate_limit_pause_remaining, poll_history, poll_once, process_message_with_retry
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
from src.contacts import (
    Contact,
    ContactCategoryError,
    ContactConflictError,
    ContactNotFoundError,
    Segment,
    SegmentConflictError,
    SegmentNotFoundError,
    create_contact,
    create_segment,
    delete_contact,
    delete_segment,
    get_contact,
    get_segment,
    import_contacts_csv,
    list_contacts as list_directory_contacts,
    list_segments,
    resolve_segment,
    update_contact,
    update_segment,
    upsert_contact,
    upsert_segment,
)
from src.run_registry import ACTIVE_RUN_STATUSES
from src.run_registry import get_run as get_run_record
from src.run_registry import delete_runs, find_run_by_email, list_runs, setup_run_registry, upsert_run
from src.gmail_sync import get_last_history_id, history_id_is_newer, set_last_history_id, setup_gmail_sync
from src.health import aggregate_health
from src.shared_cache import cache_delete_prefix, cache_get_json, cache_set_json
from src.alerts import AlertSettings, load_alert_settings, save_alert_settings
from src.retention import RetentionSettings, load_retention_settings, preview_retention, run_retention, save_retention_settings
from src.runtime_settings import RuntimeSettings, load_runtime_settings, save_runtime_settings
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
from src.campaigns import (
    CampaignTemplate,
    CampaignsConfig,
    DEFAULT_CAMPAIGNS_PATH,
    Group,
    GroupMember,
    audience_guard,
    contacts_for_segment,
    find_group,
    find_template,
    load_campaign_runs,
    load_campaigns,
    members_for_group,
    missing_variable_warnings,
    render_campaign,
    render_campaign_for_segment,
    save_campaigns,
    save_campaign_runs,
    send_campaign_run,
    upsert_campaign_run,
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
from src.style_learning import analyze_style, build_style_text, parse_style_text
from src.media import (
    delete_contact_photo,
    delete_signature_image,
    read_contact_photo,
    save_contact_photo,
    save_signature_image,
    signature_image_inline,
)
from src.ai_assist import TONES, adjust_tone, summarize_thread
from src.memory_summary import MEMORY_KINDS, memory_items, remove_item, summarize_kind
from src.persona import Persona, compiled_preview, load_persona, save_persona, suggest_persona
from src.send_mode import effective_dry_run, get_send_mode, set_send_mode
from src.signature import SIGNATURE_MODES, SignatureConfig, apply_signature, load_signature, save_signature
from src.notification_store import (
    delete_notification,
    list_notifications,
    mark_all_read,
    mark_read,
    unread_count,
)
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
            print(f"api: gmail watch registration failed: {exc}")
            record_sync_failure(str(exc))
        await asyncio.sleep(check_interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_model_redaction()
    validate_gmail_webhook_config()
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


def _request_user_id(request: Request) -> str | None:
    return request.headers.get("x-agora-user")


def _request_user_dept(request: Request) -> str | None:
    return request.headers.get("x-agora-user-dept")


def _request_agent_instance_id(request: Request) -> str | None:
    return request.headers.get("x-agora-agent-instance")


def _require_instance_role(request: Request, min_role: str) -> None:
    """Defense-in-depth: verify the gateway-stamped instance role is sufficient.

    The gateway resolves and stamps X-Agora-Instance-Role before forwarding.
    When the header is absent (direct call bypassing the gateway) no check is applied —
    the gateway is the authoritative enforcement layer; this is an additional guard only.
    Roles: owner > approver > viewer.
    """
    ROLE_TIER = {"owner": 3, "approver": 2, "viewer": 1}
    MIN_TIER = {"owner": 3, "approver": 2, "viewer": 1}
    header = request.headers.get("x-agora-instance-role")
    if header is None:
        return  # no gateway header → direct call, let it through (gateway is gatekeeper)
    caller_tier = ROLE_TIER.get(header.lower(), 0)
    required_tier = MIN_TIER.get(min_role, 99)
    if caller_tier < required_tier:
        raise HTTPException(status_code=403, detail="Insufficient instance role")


def _require_dept_access(request: Request, record: dict | None) -> None:
    if record is None:
        return
    user_dept = _request_user_dept(request)
    workflow_dept = record.get("workflow_dept")
    if user_dept and workflow_dept and workflow_dept != user_dept:
        raise HTTPException(status_code=403, detail="Not authorized for this department's approval.")


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
    # The draft as currently shown/edited in the UI; used as the redraft
    # baseline so manual edits survive a retouche.
    draft: dict | None = None


class SendModeInput(BaseModel):
    send_mode: str


class GroupInput(BaseModel):
    id: str
    name: str
    type: str = "clients"
    segment_id: str | None = None
    members: list[dict] = Field(default_factory=list)


class CampaignTemplateInput(BaseModel):
    name: str
    subject: str
    body_markdown: str
    variables: list[str] = Field(default_factory=list)
    audience: list[str] = Field(default_factory=list)
    category: str | None = None


class CampaignPrepareInput(BaseModel):
    segment_id: str | None = None
    group_id: str | None = None
    template_name: str
    name: str | None = None
    subject: str | None = None
    body_markdown: str | None = None
    scheduled_at: str | None = None
    save_as_draft: bool = False


class ContactInput(BaseModel):
    email: str
    name: str | None = None
    audience: str
    fields: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    active: bool = True
    category: str | None = None
    domain: str | None = None
    priority: str | None = None
    category_source: str = "manual"
    category_confidence: float | None = None


class SegmentInput(BaseModel):
    id: str
    name: str
    match: dict[str, str] = Field(default_factory=dict)
    members: list[str] = Field(default_factory=list)


class ContactsImportInput(BaseModel):
    csv_text: str
    audience_default: str = "client"


class CategorizeContactInput(BaseModel):
    email: str
    category: str
    domain_only: bool = False


DEFAULT_CATEGORY_PROPOSAL_STATE_PATH = SERVICE_ROOT / "logs" / "category_proposal_state.json"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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



class RulesInput(BaseModel):
    rules_yaml: str


class JunkInput(BaseModel):
    """Junk-gate settings edited from the UI (no YAML surface)."""

    enabled: bool | None = None
    allowed_senders: list[str] | None = None
    allowed_domains: list[str] | None = None
    blocked_senders: list[str] | None = None
    blocked_domains: list[str] | None = None
    gmail_categories: bool | None = None
    bulk_headers: bool | None = None
    sender_heuristics: bool | None = None


class RuleToggleInput(BaseModel):
    name: str
    enabled: bool


class SectionToggleInput(BaseModel):
    section: str
    enabled: bool


class RuleUpsertInput(BaseModel):
    """Structured add/update of a single automation rule (no YAML editing).

    ``original_name`` lets the UI rename a rule in place; when omitted the rule is
    matched (and, if absent, created) by ``name``.
    """

    name: str = Field(min_length=1)
    original_name: str | None = None
    enabled: bool = True
    when: dict = Field(default_factory=dict)
    then: dict = Field(default_factory=dict)


class RuleDeleteInput(BaseModel):
    name: str


class SectionConfigInput(BaseModel):
    """Structured update of a rules subsystem (digest/snooze/follow_ups) from a form."""

    section: str
    config: dict


class CapabilitiesInput(BaseModel):
    capabilities: dict[str, bool]


class MemoryInput(BaseModel):
    triage_preferences: str
    response_preferences: str


class StyleInput(BaseModel):
    writing_style: str


class CategoriesInput(BaseModel):
    categories_yaml: str


class RoleInput(BaseModel):
    role_key: str
    display_name: str
    emails: list[str]
    dept: str | None = None


class RuntimeSettingsInput(BaseModel):
    sync_limit: int = Field(ge=1, le=100)
    setup_recent_limit: int = Field(ge=1, le=200)
    setup_backlog_limit: int = Field(ge=1, le=100)
    setup_sent_sample: int = Field(ge=1, le=200)
    style_sent_sample: int = Field(ge=1, le=50)


def _contact_from_input(body: ContactInput) -> Contact:
    return Contact(
        email=body.email,
        name=body.name,
        audience=body.audience,
        fields=body.fields,
        tags=body.tags,
        active=body.active,
        category=body.category,
        domain=body.domain,
        priority=body.priority,
        category_source=body.category_source,
        category_confidence=body.category_confidence,
    )


def _segment_from_input(body: SegmentInput) -> Segment:
    return Segment(id=body.id, name=body.name, match=body.match, members=body.members)


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
    print(f"Unknown pending action tool name: {name}")
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


def _pending_response_after_decision_error(run_id: str, exc: Exception, action: str) -> RunResponse | None:
    record = get_run_record(
        run_id,
        user_id=None,
        agent_instance_id=current_agent_instance_id(),
    )
    if not record or record.get("status") != "pending_approval":
        return None
    print(f"api: {action} failed for run {run_id}; keeping pending approval: {exc}")
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



@app.get("/alerts/settings")
async def alerts_settings(request: Request) -> dict:
    _require_instance_role(request, "owner")
    return load_alert_settings().model_dump()


@app.put("/alerts/settings")
async def update_alerts_settings(request: Request, body: AlertSettings) -> dict:
    _require_instance_role(request, "owner")
    return save_alert_settings(body).model_dump()


@app.get("/retention/settings")
async def retention_settings(request: Request) -> dict:
    _require_instance_role(request, "owner")
    return load_retention_settings().model_dump()


@app.put("/retention/settings")
async def update_retention_settings(request: Request, body: RetentionSettings) -> dict:
    _require_instance_role(request, "owner")
    return save_retention_settings(body).model_dump()




@app.get("/runtime-settings")
async def runtime_settings(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    return load_runtime_settings(current_agent_instance_id()).model_dump()


@app.put("/runtime-settings")
async def update_runtime_settings(request: Request, body: RuntimeSettingsInput) -> dict:
    _require_instance_role(request, "owner")
    config = RuntimeSettings(**body.model_dump())
    return save_runtime_settings(config, current_agent_instance_id()).model_dump()

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
    from src.categories import Category, dump_categories

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


@app.get("/notifications")
async def list_notifications_endpoint(
    request: Request,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    _require_instance_role(request, "viewer")
    return {
        "agent_instance_id": current_agent_instance_id(),
        "notifications": list_notifications(unread_only=unread_only, limit=limit),
    }


@app.get("/notifications/unread-count")
async def notifications_unread_count(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    return {"agent_instance_id": current_agent_instance_id(), "unread_count": unread_count()}


@app.post("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: int, request: Request) -> dict:
    _require_instance_role(request, "viewer")
    row = mark_read(notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return row


@app.post("/notifications/read-all")
async def mark_all_notifications_read(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    return {"agent_instance_id": current_agent_instance_id(), "marked_read": mark_all_read()}


@app.delete("/notifications/{notification_id}")
async def delete_notification_endpoint(notification_id: int, request: Request) -> dict:
    _require_instance_role(request, "viewer")
    delete_notification(notification_id)
    return {"agent_instance_id": current_agent_instance_id(), "deleted": True}


@app.post("/retention/dry-run")
async def retention_dry_run(request: Request) -> dict:
    _require_instance_role(request, "owner")
    return await asyncio.to_thread(preview_retention)


@app.post("/retention/run")
async def retention_execute(request: Request) -> dict:
    _require_instance_role(request, "owner")
    retention_config = await asyncio.to_thread(load_retention_settings)
    if retention_config.retention_days <= 0:
        raise HTTPException(status_code=400, detail="retention disabled; set retention_days > 0 before executing")
    return await asyncio.to_thread(run_retention)


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
        print(f"api: outlook oauth callback rejected: {exc}")
        instance_id = payload["agent_instance_id"] if payload else None
        return _oauth_callback_redirect("outlook", instance_id, "error", str(exc))
    except Exception as exc:
        print(f"api: outlook oauth callback failed: {exc!r}\n{traceback.format_exc()}")
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
        print(f"api: failed to start onboarding for {user_id}/{agent_instance_id}: {exc}")


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
        print(f"api: gmail oauth callback rejected: {exc}")
        instance_id = payload["agent_instance_id"] if payload else None
        return _gmail_callback_redirect(instance_id, "error", str(exc))
    except Exception as exc:
        # Token exchange reaches out to Google; a transient network failure (or a
        # stale/replayed single-use code) must not surface as a raw 500.
        print(f"api: gmail oauth callback failed: {exc!r}\n{traceback.format_exc()}")
        instance_id = payload["agent_instance_id"] if payload else None
        return _gmail_callback_redirect(
            instance_id,
            "error",
            f"Could not finish connecting to Google ({type(exc).__name__}: {exc}). Try again.",
        )
    return _gmail_callback_redirect(payload["agent_instance_id"], "connected")


def _serialize_role(role) -> dict:
    return role.model_dump() | {"primary_email": role.primary_email}


def _serialize_contact(contact: Contact) -> dict:
    return contact.model_dump()


def _serialize_segment(segment: Segment) -> dict:
    resolved = resolve_segment(segment, agent_instance_id=current_agent_instance_id())
    return segment.model_dump() | {
        "resolved_count": len(resolved),
        "resolved_members": [member.model_dump() for member in resolved],
    }


def _serialize_group(group: Group) -> dict:
    members = members_for_group(group, agent_instance_id=current_agent_instance_id())
    return group.model_dump() | {
        "segment_id": group.segment_id or group.id,
        "members": [member.model_dump() for member in members],
        "member_count": len(members),
    }


def _current_categories() -> tuple[str, CategoriesConfig]:
    categories_yaml = read_instance_text("categories", DEFAULT_CATEGORIES_PATH)
    data = yaml.safe_load(categories_yaml) or {}
    data.setdefault("categories", [])
    data.setdefault("templates", [])
    data.setdefault("contacts", [])
    return categories_yaml, CategoriesConfig(**data)


@app.get("/categories")
async def get_categories() -> dict:
    categories_yaml, cfg = _current_categories()
    return {
        "agent_instance_id": current_agent_instance_id(),
        "categories_yaml": categories_yaml or dump_categories(cfg),
        "parsed": cfg.model_dump(),
        "storage": "instance-config",
    }


@app.put("/categories")
async def update_categories(body: CategoriesInput) -> dict:
    data = yaml.safe_load(body.categories_yaml) or {}
    data.setdefault("categories", [])
    data.setdefault("templates", [])
    data.setdefault("contacts", [])
    parsed = CategoriesConfig(**data)
    write_instance_text("categories", body.categories_yaml, DEFAULT_CATEGORIES_PATH)
    return {
        "agent_instance_id": current_agent_instance_id(),
        "categories_yaml": body.categories_yaml,
        "parsed": parsed.model_dump(),
        "storage": "instance-config",
    }

class CategoryUpdateInput(BaseModel):
    display_name: str
    description: str | None = None
    enabled: bool = True
    priority: str = "normal"
    policy: str = "notify"
    owner: str | None = None
    approver: str | None = None
    route_to: list[str] = Field(default_factory=list)
    instructions: dict | None = None
    when: dict | None = None
    template: str | None = None
    require_approval: bool = False
    external_send_allowed: bool = True


class CategoryProposalActionInput(BaseModel):
    proposal_id: str
    name: str | None = None
    display_name: str | None = None


def _slugify_category(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "nouvelle_categorie"


def _proposal_subject_token(subject: str, snippet: str = "") -> tuple[str, str] | None:
    text = f"{subject} {snippet}".lower()
    patterns = [
        ("factures", ("facture", "invoice", "reçu", "receipt", "paiement", "payment")),
        ("candidatures", ("candidature", "cv", "stage", "emploi", "recrutement", "candidate")),
        ("rendez_vous", ("rendez-vous", "rdv", "meeting", "calendrier", "appointment")),
        ("support", ("incident", "problème", "bug", "support", "panne", "erreur")),
        ("contrats", ("contrat", "contract", "signature", "devis", "quote")),
    ]
    for name, needles in patterns:
        if any(needle in text for needle in needles):
            return name, needles[0]
    return None


def _proposal_key(kind: str, value: str) -> str:
    return f"{kind}:{value}".lower()


def _dismissed_category_proposals() -> set[str]:
    raw = read_instance_text("category_proposal_state", DEFAULT_CATEGORY_PROPOSAL_STATE_PATH)
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    return {str(item) for item in data.get("dismissed", [])}


def _save_dismissed_category_proposals(dismissed: set[str]) -> None:
    write_instance_text(
        "category_proposal_state",
        json.dumps({"dismissed": sorted(dismissed)}, indent=2, sort_keys=True),
        DEFAULT_CATEGORY_PROPOSAL_STATE_PATH,
    )


# Clustering by sender domain only says something when the domain belongs to an
# organisation. A consumer mailbox provider groups unrelated people, and accepting
# it would create a `sender_domain` rule that swallows most personal mail.
CONSUMER_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.fr",
    "live.com", "live.fr", "msn.com", "yahoo.com", "yahoo.fr", "ymail.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "protonmail.com", "proton.me",
    "gmx.com", "gmx.fr", "gmx.net", "mail.com", "zoho.com", "yandex.com",
    "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net", "bbox.fr",
})


def _category_proposals_from_messages(messages: list[dict], existing_names: set[str], dismissed: set[str]) -> list[dict]:
    by_domain: dict[str, list[dict]] = {}
    by_subject: dict[str, dict] = {}
    for msg in messages:
        _name, address = parseaddr(msg.get("from") or msg.get("author") or "")
        domain = address.rsplit("@", 1)[1].lower() if "@" in address else ""
        if domain and domain not in CONSUMER_MAIL_DOMAINS:
            by_domain.setdefault(domain, []).append(msg)
        token = _proposal_subject_token(str(msg.get("subject") or ""), str(msg.get("snippet") or ""))
        if token:
            category_name, keyword = token
            bucket = by_subject.setdefault(category_name, {"keyword": keyword, "messages": []})
            bucket["messages"].append(msg)

    proposals: list[dict] = []
    for domain, bucket in by_domain.items():
        if len(bucket) < 3:
            continue
        name = _slugify_category(domain.split(".")[-2] if "." in domain else domain)
        key = _proposal_key("domain", domain)
        if name in existing_names or key in dismissed:
            continue
        proposals.append({
            "id": key,
            "kind": "domain",
            "suggested_name": name,
            "display_name": domain,
            "description": f"{len(bucket)} messages récents depuis {domain}",
            "message_count": len(bucket),
            "when": {"sender_domain": [domain]},
            "sample_subjects": [m.get("subject") or "(sans objet)" for m in bucket[:3]],
        })

    for name, bucket in by_subject.items():
        key = _proposal_key("subject", name)
        messages = bucket["messages"]
        if len(messages) < 2 or name in existing_names or key in dismissed:
            continue
        proposals.append({
            "id": key,
            "kind": "subject_pattern",
            "suggested_name": name,
            "display_name": name.replace("_", " ").capitalize(),
            "description": f"{len(messages)} messages récents autour de « {bucket['keyword']} »",
            "message_count": len(messages),
            "when": {"subject_contains": [bucket["keyword"]]},
            "sample_subjects": [m.get("subject") or "(sans objet)" for m in messages[:3]],
        })

    proposals.sort(key=lambda item: (-item["message_count"], item["suggested_name"]))
    return proposals[:20]


async def _build_category_proposals(limit: int = 500) -> dict:
    _yaml_text, cfg = _current_categories()
    existing_names = {category.name for category in cfg.categories}
    try:
        messages = await asyncio.to_thread(get_provider().list_inbox, limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Gmail metadata scan unavailable: {exc}") from exc
    dismissed = _dismissed_category_proposals()
    return {
        "agent_instance_id": current_agent_instance_id(),
        "scanned": len(messages),
        "proposals": _category_proposals_from_messages(messages, existing_names, dismissed),
    }


@app.get("/categories/proposals")
async def category_proposals(limit: int = Query(default=500, ge=25, le=500)) -> dict:
    """Discover category proposals from recent Gmail metadata only."""
    return await _build_category_proposals(limit)


@app.post("/categories/proposals/accept")
async def accept_category_proposal(body: CategoryProposalActionInput, request: Request) -> dict:
    _require_instance_role(request, "owner")
    proposals = (await _build_category_proposals()).get("proposals", [])
    proposal = next((item for item in proposals if item["id"] == body.proposal_id), None)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Category proposal not found")
    _yaml_text, cfg = _current_categories()
    name = _slugify_category(body.name or proposal["suggested_name"])
    if any(category.name == name for category in cfg.categories):
        raise HTTPException(status_code=409, detail="Category already exists")
    from src.automation import RuleWhen

    cfg.categories.append(Category(
        name=name,
        display_name=(body.display_name or proposal["display_name"]).strip(),
        description=proposal["description"],
        enabled=False,
        priority="normal",
        policy="notify",
        when=RuleWhen(**proposal["when"]),
    ))
    write_instance_text("categories", dump_categories(cfg), DEFAULT_CATEGORIES_PATH)
    dismissed = _dismissed_category_proposals()
    dismissed.add(body.proposal_id)
    _save_dismissed_category_proposals(dismissed)
    return {"accepted": proposal, "parsed": cfg.model_dump()}


@app.post("/categories/proposals/dismiss")
async def dismiss_category_proposal(body: CategoryProposalActionInput, request: Request) -> dict:
    _require_instance_role(request, "owner")
    dismissed = _dismissed_category_proposals()
    dismissed.add(body.proposal_id)
    _save_dismissed_category_proposals(dismissed)
    return {"dismissed": body.proposal_id}


@app.put("/categories/{name}")
async def update_category_endpoint(name: str, body: CategoryUpdateInput, request: Request) -> dict:
    _require_instance_role(request, "owner")
    from src.categories import Category, CategoryInstructions
    categories_yaml, cfg = _current_categories()
    for cat in cfg.categories:
        if cat.name == name:
            cat.display_name = body.display_name
            cat.description = body.description
            cat.enabled = body.enabled
            cat.priority = body.priority
            cat.policy = body.policy
            cat.owner = body.owner
            cat.approver = body.approver
            cat.route_to = body.route_to
            cat.require_approval = body.require_approval
            cat.external_send_allowed = body.external_send_allowed
            if body.instructions:
                cat.instructions = CategoryInstructions(**body.instructions)
            else:
                cat.instructions = None
            if body.when is not None:
                from src.automation import RuleWhen
                cat.when = RuleWhen(**body.when)
            cat.template = body.template
            break
    else:
        raise HTTPException(status_code=404, detail="Category not found")
    new_yaml = dump_categories(cfg)
    write_instance_text("categories", new_yaml, DEFAULT_CATEGORIES_PATH)
    return {
        "agent_instance_id": current_agent_instance_id(),
        "parsed": cfg.model_dump(),
        "storage": "instance-config",
    }


@app.delete("/categories/{name}")
async def delete_category_endpoint(name: str, request: Request) -> dict:
    _require_instance_role(request, "owner")
    categories_yaml, cfg = _current_categories()
    initial_len = len(cfg.categories)
    cfg.categories = [cat for cat in cfg.categories if cat.name != name]
    if len(cfg.categories) == initial_len:
        raise HTTPException(status_code=404, detail="Category not found")
    new_yaml = dump_categories(cfg)
    write_instance_text("categories", new_yaml, DEFAULT_CATEGORIES_PATH)
    return {
        "agent_instance_id": current_agent_instance_id(),
        "parsed": cfg.model_dump(),
        "storage": "instance-config",
    }


@app.post("/categories/{name}/duplicate")
async def duplicate_category_endpoint(name: str, request: Request) -> dict:
    _require_instance_role(request, "owner")
    categories_yaml, cfg = _current_categories()
    source = next((cat for cat in cfg.categories if cat.name == name), None)
    if source is None:
        raise HTTPException(status_code=404, detail="Category not found")

    existing_names = {cat.name for cat in cfg.categories}
    base_name = f"{source.name}_copy"
    new_name = base_name
    suffix = 2
    while new_name in existing_names:
        new_name = f"{base_name}_{suffix}"
        suffix += 1

    clone = source.model_copy(deep=True)
    clone.name = new_name
    clone.display_name = f"{source.display_name} (copy)"
    cfg.categories.append(clone)

    new_yaml = dump_categories(cfg)
    write_instance_text("categories", new_yaml, DEFAULT_CATEGORIES_PATH)
    return {
        "agent_instance_id": current_agent_instance_id(),
        "parsed": cfg.model_dump(),
        "storage": "instance-config",
        "new_name": new_name,
    }


class CategoryTestMatchInput(BaseModel):
    author: str = ""
    subject: str = ""
    email_thread: str = ""


@app.post("/categories/test-match")
async def test_category_match(body: CategoryTestMatchInput, request: Request) -> dict:
    """Preview which workflow (if any) a sample email would match, without sending anything."""
    _require_instance_role(request, "owner")
    _categories_yaml, cfg = _current_categories()
    result = classify_category(
        {"author": body.author, "subject": body.subject, "email_thread": body.email_thread},
        cfg,
    )
    contact = result.get("contact")
    return {
        "matched": result.get("category") is not None,
        "category": result.get("category"),
        "category_display_name": result.get("category_display_name"),
        "priority": result.get("priority"),
        "policy": result.get("policy"),
        "owner": result.get("owner"),
        "approver": result.get("approver"),
        "route_to": result.get("route_to") or [],
        "instructions": result.get("instructions"),
        "contact": contact.model_dump() if contact else None,
    }


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


@app.get("/contacts")
async def get_contacts(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    contacts = list_directory_contacts()
    return {
        "agent_instance_id": current_agent_instance_id(),
        "contacts": [_serialize_contact(contact) for contact in contacts],
        "storage": "contacts-directory",
    }


@app.post("/contacts", status_code=201)
async def create_contact_entry(request: Request, body: ContactInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        contact = create_contact(_contact_from_input(body))
    except ContactConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ContactCategoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "contact": _serialize_contact(contact),
        "storage": "contacts-directory",
    }


@app.put("/contacts/{email}")
async def update_contact_entry(email: str, request: Request, body: ContactInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        contact = update_contact(email, _contact_from_input(body))
    except ContactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ContactCategoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "contact": _serialize_contact(contact),
        "storage": "contacts-directory",
    }


@app.delete("/contacts/{email}")
async def delete_contact_entry(email: str, request: Request) -> dict:
    _require_instance_role(request, "owner")
    try:
        delete_contact(email)
    except ContactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "deleted": email.strip().lower(),
        "storage": "contacts-directory",
    }


@app.post("/contacts/{email}/photo")
async def upload_contact_photo(email: str, request: Request, file: UploadFile = File(...)) -> dict:
    _require_instance_role(request, "owner")
    data = await file.read()
    try:
        stored = await asyncio.to_thread(save_contact_photo, email, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "email": email.strip().lower(),
        "stored": True,
        "size": stored.size,
    }


@app.get("/contacts/{email}/photo")
async def get_contact_photo(email: str) -> Response:
    data = await asyncio.to_thread(read_contact_photo, email)
    if data is None:
        raise HTTPException(status_code=404, detail="No photo for this contact")
    return Response(content=data, media_type="image/jpeg")


@app.delete("/contacts/{email}/photo")
async def remove_contact_photo(email: str, request: Request) -> dict:
    _require_instance_role(request, "owner")
    removed = delete_contact_photo(email)
    return {
        "agent_instance_id": current_agent_instance_id(),
        "email": email.strip().lower(),
        "removed": removed,
    }


@app.post("/contacts/import")
async def import_contacts_entries(request: Request, body: ContactsImportInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        result = import_contacts_csv(body.csv_text, audience_default=body.audience_default)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "imported": [_serialize_contact(contact) for contact in result["imported"]],
        "rejected": result["rejected"],
        "imported_count": result["imported_count"],
        "rejected_count": result["rejected_count"],
        "storage": "contacts-directory",
    }


@app.post("/contacts/migrate-legacy")
async def migrate_legacy_contacts_endpoint(request: Request) -> dict:
    _require_instance_role(request, "owner")
    from src.contacts import migrate_legacy_category_contacts

    result = await asyncio.to_thread(migrate_legacy_category_contacts)
    return {"agent_instance_id": current_agent_instance_id(), **result}


@app.post("/contacts/categorize")
async def categorize_contact_endpoint(request: Request, body: CategorizeContactInput) -> dict:
    """One-action 'categoriser l'expéditeur' / 'categoriser le domaine' from the inbox row.

    Sender-scoped requests upsert the unified contact directory (email always
    present there). Domain-only requests can't live in the directory (its email
    field is required) so they upsert a legacy categories.yaml domain contact
    instead — still picked up by classify_category's precedence-4 domain match.
    """
    _require_instance_role(request, "owner")
    from email.utils import parseaddr

    _, address = parseaddr(body.email)
    address = (address or body.email).strip().lower()
    if "@" not in address:
        raise HTTPException(status_code=400, detail="email must contain an address to categorize")

    if body.domain_only:
        domain = address.rsplit("@", 1)[-1]
        cfg = await asyncio.to_thread(load_categories, None, current_agent_instance_id())
        cfg.contacts = [c for c in cfg.contacts if not (c.domain and c.domain.lower() == domain and not c.email)]
        cfg.contacts.append(LegacyCategoryContact(domain=domain, category=body.category))
        write_instance_text("categories", dump_categories(cfg), DEFAULT_CATEGORIES_PATH)
        return {"agent_instance_id": current_agent_instance_id(), "domain": domain, "category": body.category}

    try:
        contact = upsert_contact(Contact(email=address, audience="prospect", category=body.category, category_source="manual"))
    except ContactCategoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"agent_instance_id": current_agent_instance_id(), "contact": _serialize_contact(contact)}


@app.get("/segments")
async def get_segments(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    segments = list_segments()
    return {
        "agent_instance_id": current_agent_instance_id(),
        "segments": [_serialize_segment(segment) for segment in segments],
        "storage": "contacts-directory",
    }


@app.post("/segments", status_code=201)
async def create_segment_entry(request: Request, body: SegmentInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        segment = create_segment(_segment_from_input(body))
    except SegmentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "segment": _serialize_segment(segment),
        "storage": "contacts-directory",
    }


@app.put("/segments/{segment_id}")
async def update_segment_entry(segment_id: str, request: Request, body: SegmentInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        segment = update_segment(segment_id, _segment_from_input(body))
    except SegmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "segment": _serialize_segment(segment),
        "storage": "contacts-directory",
    }


@app.delete("/segments/{segment_id}")
async def delete_segment_entry(segment_id: str, request: Request) -> dict:
    _require_instance_role(request, "owner")
    try:
        delete_segment(segment_id)
    except SegmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "deleted": segment_id.strip(),
        "storage": "contacts-directory",
    }


# ---------------------------------------------------------------------------
# Outbound broadcast campaigns (segment-targeted + approval-gated).
# ---------------------------------------------------------------------------


@app.get("/campaigns/groups")
async def list_groups() -> dict:
    cfg = load_campaigns()
    return {
        "agent_instance_id": current_agent_instance_id(),
        "groups": [_serialize_group(group) for group in cfg.groups],
    }


@app.post("/campaigns/groups")
async def upsert_group(request: Request, body: GroupInput) -> dict:
    _require_instance_role(request, "owner")
    cfg = load_campaigns()
    group = Group(
        id=body.id,
        name=body.name,
        type=body.type if body.type in ("employees", "clients") else "clients",
        segment_id=body.segment_id or body.id,
        members=[GroupMember(**member) for member in body.members],
    )
    if group.members:
        default_audience = "employee" if group.type == "employees" else "client"
        for member in group.members:
            existing = get_contact(member.email)
            contact = Contact(
                email=member.email,
                name=member.name or (existing.name if existing else None),
                audience=existing.audience if existing else default_audience,
                fields=((existing.fields if existing else {}) | member.fields),
                tags=list(existing.tags) if existing else [],
                active=existing.active if existing else True,
            )
            upsert_contact(contact)
        upsert_segment(Segment(id=group.segment_id or group.id, name=group.name, members=[member.email for member in group.members], match={}))
    elif get_segment(group.segment_id or group.id) is None:
        raise HTTPException(status_code=404, detail="segment not found")
    cfg.groups = [item for item in cfg.groups if item.id != group.id] + [Group(id=group.id, name=group.name, type=group.type, segment_id=group.segment_id)]
    save_campaigns(cfg)
    return {"group": _serialize_group(Group(id=group.id, name=group.name, type=group.type, segment_id=group.segment_id))}


@app.delete("/campaigns/groups/{group_id}")
async def delete_group(request: Request, group_id: str) -> dict:
    _require_instance_role(request, "owner")
    cfg = load_campaigns()
    group = find_group(cfg, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    cfg.groups = [item for item in cfg.groups if item.id != group_id]
    save_campaigns(cfg)
    segment_id = group.segment_id or group.id
    if segment_id and not any((item.segment_id or item.id) == segment_id for item in cfg.groups):
        try:
            delete_segment(segment_id)
        except SegmentNotFoundError:
            pass
    return {"deleted": group_id}


@app.get("/campaigns/templates")
async def list_campaign_templates() -> dict:
    cfg = load_campaigns()
    return {"templates": [t.model_dump() for t in cfg.templates]}


@app.post("/campaigns/templates")
async def upsert_campaign_template(request: Request, body: CampaignTemplateInput) -> dict:
    _require_instance_role(request, "owner")
    cfg = load_campaigns()
    template = CampaignTemplate(
        name=body.name,
        subject=body.subject,
        body_markdown=body.body_markdown,
        variables=body.variables,
        audience=body.audience,
        category=body.category,
    )
    cfg.templates = [t for t in cfg.templates if t.name != template.name] + [template]
    save_campaigns(cfg)
    return {"template": template.model_dump()}


@app.delete("/campaigns/templates/{name}")
async def delete_campaign_template(request: Request, name: str) -> dict:
    _require_instance_role(request, "owner")
    cfg = load_campaigns()
    before = len(cfg.templates)
    cfg.templates = [t for t in cfg.templates if t.name != name]
    if len(cfg.templates) == before:
        raise HTTPException(status_code=404, detail="template not found")
    save_campaigns(cfg)
    return {"deleted": name}


def _campaign_target(cfg: CampaignsConfig, body: CampaignPrepareInput) -> tuple[str, str]:
    if body.segment_id:
        segment = get_segment(body.segment_id, agent_instance_id=current_agent_instance_id())
        if segment is None:
            raise HTTPException(status_code=404, detail="segment not found")
        return segment.id, segment.name
    if body.group_id:
        group = find_group(cfg, body.group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="group not found")
        segment = get_segment(group.segment_id or group.id, agent_instance_id=current_agent_instance_id())
        if segment is None:
            raise HTTPException(status_code=404, detail="segment not found")
        return segment.id, segment.name
    raise HTTPException(status_code=422, detail="segment_id is required")


def _campaign_preview_payload(cfg: CampaignsConfig, body: CampaignPrepareInput) -> dict:
    segment_id, segment_name = _campaign_target(cfg, body)
    template = find_template(cfg, body.template_name)
    if template is None:
        raise HTTPException(status_code=404, detail="template not found")
    if body.subject or body.body_markdown:
        template = template.model_copy(update={
            "subject": body.subject or template.subject,
            "body_markdown": body.body_markdown or template.body_markdown,
        })

    contacts = contacts_for_segment(segment_id, agent_instance_id=current_agent_instance_id())
    if not contacts:
        raise HTTPException(status_code=400, detail="Le segment sélectionné ne contient aucun destinataire actif.")

    rendered_models = render_campaign_for_segment(segment_id, template, agent_instance_id=current_agent_instance_id())
    warnings = missing_variable_warnings(rendered_models)
    guard = audience_guard(template, contacts, segment_name=segment_name)
    rendered = [item.model_dump() for item in rendered_models]
    return {
        "segment_id": segment_id,
        "segment_name": segment_name,
        "template_name": template.name,
        "campaign_name": body.name or template.name,
        "subject": body.subject or template.subject,
        "scheduled_at": body.scheduled_at,
        "template_category": template.category,
        "template_audience": list(template.audience),
        "segment_audiences": guard.segment_audiences,
        "recipient_count": len(rendered),
        "preview": rendered[0] if rendered else None,
        "recipients": [
            {"email": item["email"], "name": item.get("name"), "subject": item["subject"], "unresolved": item.get("unresolved") or []}
            for item in rendered
        ],
        "missing_variables": [warning.model_dump() for warning in warnings],
        "audience_match": guard.allowed,
        "guard_message": guard.reason,
        "rendered": rendered,
    }


def _campaign_summary(campaign_id: str, record: dict) -> dict:
    rendered = record.get("rendered") or []
    return {
        "campaign_id": campaign_id,
        "status": record["status"],
        "campaign_name": record.get("campaign_name") or record["template_name"],
        "subject": record.get("subject"),
        "scheduled_at": record.get("scheduled_at"),
        "group_id": record.get("group_id"),
        "group_name": record.get("group_name"),
        "segment_id": record.get("segment_id"),
        "segment_name": record.get("segment_name"),
        "template_name": record["template_name"],
        "template_category": record.get("template_category"),
        "template_audience": record.get("template_audience") or [],
        "segment_audiences": record.get("segment_audiences") or [],
        "recipient_count": len(rendered),
        "created_at": record["created_at"],
        "updated_at": record.get("updated_at"),
        "sent_at": record.get("sent_at"),
        "preview": rendered[0] if rendered else None,
        "recipients": [
            {
                "email": r["email"],
                "name": r.get("name"),
                "subject": r["subject"],
                "unresolved": r.get("unresolved") or [],
                "status": _recipient_status(r["email"], record.get("result")),
            }
            for r in rendered
        ],
        "missing_variables": record.get("missing_variables") or [],
        "audience_match": record.get("audience_match", True),
        "guard_message": record.get("guard_message"),
        "result": record.get("result"),
    }


def _recipient_status(email: str, result: dict | None) -> str:
    if not result:
        return "pending"
    for item in result.get("sent") or []:
        if item.get("email") == email:
            return "sent"
    for item in result.get("denied") or []:
        if item.get("email") == email:
            return "denied"
    for item in result.get("failed") or []:
        if item.get("email") == email:
            return "failed"
    return "pending"


def _campaign_records_for_current_instance() -> dict[str, dict]:
    instance = current_agent_instance_id()
    return {
        cid: rec
        for cid, rec in load_campaign_runs(agent_instance_id=instance).items()
        if rec.get("agent_instance_id") == instance
    }


@app.get("/campaigns")
async def list_campaigns() -> dict:
    instance = current_agent_instance_id()
    items = [
        _campaign_summary(cid, rec)
        for cid, rec in _campaign_records_for_current_instance().items()
    ]
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return {"agent_instance_id": instance, "campaigns": items}


@app.post("/campaigns/preview")
async def preview_campaign(request: Request, body: CampaignPrepareInput) -> dict:
    _require_instance_role(request, "owner")
    cfg = load_campaigns()
    preview = _campaign_preview_payload(cfg, body)
    preview.pop("rendered", None)
    return preview


@app.post("/campaigns/prepare")
async def prepare_campaign(request: Request, body: CampaignPrepareInput) -> dict:
    _require_instance_role(request, "owner")
    cfg = load_campaigns()
    preview = _campaign_preview_payload(cfg, body)
    if not body.save_as_draft and not preview["audience_match"]:
        raise HTTPException(status_code=422, detail=preview["guard_message"])
    if not body.save_as_draft and preview["missing_variables"]:
        raise HTTPException(
            status_code=422,
            detail=(
                "Variables manquantes pour certains destinataires : "
                + "; ".join(
                    f"{item['email']} ({', '.join(item['unresolved'])})"
                    for item in preview["missing_variables"]
                )
            ),
        )

    campaign_id = str(uuid.uuid4())
    status = "draft" if body.save_as_draft else "pending_approval"
    record = {
        "status": status,
        "campaign_name": preview.get("campaign_name") or preview["template_name"],
        "group_id": body.group_id,
        "group_name": None,
        "segment_id": preview["segment_id"],
        "segment_name": preview["segment_name"],
        "template_name": preview["template_name"],
        "subject": preview.get("subject"),
        "scheduled_at": preview.get("scheduled_at"),
        "template_category": preview["template_category"],
        "template_audience": preview["template_audience"],
        "segment_audiences": preview["segment_audiences"],
        "missing_variables": preview["missing_variables"],
        "audience_match": preview["audience_match"],
        "guard_message": preview["guard_message"],
        "rendered": preview["rendered"],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "user_id": current_user_id(),
        "agent_instance_id": current_agent_instance_id(),
    }
    upsert_campaign_run(campaign_id, record, agent_instance_id=current_agent_instance_id())
    return _campaign_summary(campaign_id, record)


@app.post("/campaigns/{campaign_id}/reject")
async def reject_campaign(request: Request, campaign_id: str) -> dict:
    _require_instance_role(request, "owner")
    records = load_campaign_runs(agent_instance_id=current_agent_instance_id())
    record = records.get(campaign_id)
    if record is None or record.get("agent_instance_id") != current_agent_instance_id():
        raise HTTPException(status_code=404, detail="campaign not found")
    record["status"] = "cancelled"
    record["updated_at"] = _now_iso()
    records[campaign_id] = record
    save_campaign_runs(records, agent_instance_id=current_agent_instance_id())
    return _campaign_summary(campaign_id, record)


@app.post("/campaigns/{campaign_id}/approve")
async def approve_campaign(request: Request, campaign_id: str) -> dict:
    _require_instance_role(request, "owner")
    records = load_campaign_runs(agent_instance_id=current_agent_instance_id())
    record = records.get(campaign_id)
    if record is None or record.get("agent_instance_id") != current_agent_instance_id():
        raise HTTPException(status_code=404, detail="campaign not found")
    if record["status"] != "pending_approval":
        if record["status"] == "draft":
            record["status"] = "pending_approval"
        else:
            raise HTTPException(status_code=409, detail=f"campaign already {record['status']}")
    if not record.get("audience_match", True):
        raise HTTPException(status_code=422, detail=record.get("guard_message") or "Audience incompatible")
    if record.get("missing_variables"):
        raise HTTPException(status_code=422, detail="Variables manquantes détectées avant l'envoi")

    if record.get("scheduled_at"):
        record["status"] = "scheduled"
        record["updated_at"] = _now_iso()
        records[campaign_id] = record
        save_campaign_runs(records, agent_instance_id=current_agent_instance_id())
        return _campaign_summary(campaign_id, record)

    send_campaign_run(campaign_id, record)
    record["updated_at"] = _now_iso()
    records[campaign_id] = record
    save_campaign_runs(records, agent_instance_id=current_agent_instance_id())
    summary = _campaign_summary(campaign_id, record)
    return summary


def sweep_due_campaigns(now: datetime | None = None, agent_instance_id: str | None = None) -> list[dict]:
    from src.campaigns import due_campaign_ids

    instance = agent_instance_id or current_agent_instance_id()
    records = load_campaign_runs(agent_instance_id=instance)
    changed = False
    summaries: list[dict] = []
    for campaign_id in due_campaign_ids(now=now, agent_instance_id=instance):
        record = records.get(campaign_id)
        if not record or record.get("agent_instance_id") != instance:
            continue
        try:
            send_campaign_run(campaign_id, record)
        except Exception as exc:
            record["status"] = "failed"
            record["result"] = {"sent": [], "denied": [], "failed": [{"email": None, "error": str(exc)}]}
        record["updated_at"] = _now_iso()
        records[campaign_id] = record
        changed = True
        summaries.append(_campaign_summary(campaign_id, record))
    if changed:
        save_campaign_runs(records, agent_instance_id=instance)
    return summaries


def _run_timestamp_at_or_after(record: dict, since_dt: datetime) -> bool:
    value = record.get("created_at") or record.get("updated_at")
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) >= since_dt


@app.get("/drafts")
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


@app.get("/junk/suggestions")
async def junk_suggestions(request: Request, limit: int = Query(default=200, ge=25, le=500)) -> dict:
    """Block candidates taken from this mailbox's own traffic, not placeholders."""
    _require_instance_role(request, "viewer")
    config = load_junk(agent_instance_id=current_agent_instance_id())
    try:
        messages = await asyncio.to_thread(get_provider().list_inbox, limit)
    except Exception as exc:
        print(f"api: junk suggestions unavailable: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Gmail inbox is unavailable. Check OAuth credentials and container network access.",
        ) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "scanned": len(messages),
        "suggestions": suggest_junk_senders(messages, config),
    }


@app.get("/junk")
async def get_junk(request: Request) -> dict:
    """Junk-gate settings for this instance, plus the reasons the gate can report."""
    _require_instance_role(request, "viewer")
    config = load_junk(agent_instance_id=current_agent_instance_id())
    return {
        "agent_instance_id": current_agent_instance_id(),
        "junk": config.model_dump(),
    }


@app.put("/junk")
async def update_junk(request: Request, body: JunkInput) -> dict:
    """Patch junk-gate settings; omitted fields keep their current value."""
    _require_instance_role(request, "owner")
    current = load_junk(agent_instance_id=current_agent_instance_id())
    patch = body.model_dump(exclude_none=True)
    for key in ("allowed_senders", "allowed_domains", "blocked_senders", "blocked_domains"):
        if key in patch:
            patch[key] = [item.strip().lower() for item in patch[key] if item and item.strip()]
    updated = JunkConfig(**{**current.model_dump(), **patch})
    save_junk(updated, agent_instance_id=current_agent_instance_id())
    return {
        "agent_instance_id": current_agent_instance_id(),
        "junk": updated.model_dump(),
    }


@app.get("/rules")
async def get_rules() -> dict:
    rules_yaml = read_instance_text("rules", DEFAULT_RULES_PATH) or "enabled: false\n"
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
    write_instance_text("rules", body.rules_yaml, DEFAULT_RULES_PATH)
    return {
        "rules_yaml": body.rules_yaml,
        "parsed": parsed.model_dump(),
    }


def _suggestions_path():
    rel = load_rules().learning.suggestions_path or "logs/rule_suggestions.jsonl"
    return SERVICE_ROOT / rel


def _read_suggestions() -> list[dict]:
    path = _suggestions_path()
    if not path.is_file():
        return []
    valid: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            valid.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return valid


def _write_suggestions(items: list[dict]) -> None:
    _suggestions_path().write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in items))


def _merge_rule_when(existing, learned_when: dict | None):
    from src.automation import RuleWhen

    base = existing.model_dump() if hasattr(existing, "model_dump") else RuleWhen().model_dump()
    candidate = RuleWhen(**(learned_when or {})).model_dump()
    merged: dict[str, list[str]] = {}
    for field in ("sender_contains", "sender_domain", "subject_contains", "labels"):
        values = []
        seen: set[str] = set()
        for raw in [*(base.get(field) or []), *(candidate.get(field) or [])]:
            cleaned = str(raw).strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            values.append(cleaned)
        merged[field] = values
    return RuleWhen(**merged)


def _promote_workflow_suggestion(item: dict) -> dict:
    from src.automation import RuleWhen
    from src.categories import Category, CategoryInstructions

    payload = item.get("suggested_workflow") or {}
    if not payload:
        raise HTTPException(status_code=422, detail="Workflow suggestion is empty")

    categories_yaml, cfg = _current_categories()
    existing = next((cat for cat in cfg.categories if cat.name == payload.get("name")), None)
    instructions_payload = payload.get("instructions") or {}
    route_to = [str(value).strip() for value in (payload.get("route_to") or []) if str(value).strip()]

    if existing is not None:
        if payload.get("display_name"):
            existing.display_name = payload["display_name"]
        if payload.get("priority"):
            existing.priority = payload["priority"]
        if payload.get("policy"):
            existing.policy = payload["policy"]
        if payload.get("owner"):
            existing.owner = payload["owner"]
        if payload.get("approver"):
            existing.approver = payload["approver"]
        if route_to:
            existing.route_to = route_to
        if payload.get("when"):
            existing.when = _merge_rule_when(existing.when, payload.get("when"))
        if instructions_payload:
            existing.instructions = CategoryInstructions(**instructions_payload)
        promoted = existing
    else:
        promoted = Category(
            name=str(payload.get("name") or "learned_workflow").strip(),
            display_name=str(payload.get("display_name") or payload.get("name") or "Workflow appris").strip(),
            priority=payload.get("priority") or "normal",
            policy=payload.get("policy") or "notify",
            owner=payload.get("owner"),
            approver=payload.get("approver"),
            route_to=route_to,
            when=RuleWhen(**(payload.get("when") or {})),
            instructions=CategoryInstructions(**instructions_payload) if instructions_payload else None,
        )
        cfg.categories.append(promoted)

    write_instance_text("categories", dump_categories(cfg), DEFAULT_CATEGORIES_PATH)
    return {"kind": "workflow", "promoted": promoted.model_dump(), "categories": cfg.model_dump()}


def _persist_rules(config: RulesConfig) -> dict:
    # Structured dump (comments are not preserved — the YAML stays valid). The raw
    # editor is still available for hand-tuning with comments.
    write_instance_text(
        "rules", yaml.safe_dump(config.model_dump(), sort_keys=False), DEFAULT_RULES_PATH
    )
    return {"parsed": load_rules().model_dump()}


class StarterRulesInput(BaseModel):
    ids: list[str] = Field(default_factory=list)


@app.get("/rules/starter")
async def get_starter_rules() -> dict:
    """Sensible starter rules for a small company, offered rather than forced."""
    return {"starter_rules": starter_rule_catalogue(load_rules())}


@app.post("/rules/starter/apply")
async def apply_starter_rules_endpoint(request: Request, body: StarterRulesInput) -> dict:
    _require_instance_role(request, "owner")
    if not body.ids:
        raise HTTPException(status_code=400, detail="Select at least one starter rule")
    config = load_rules()
    try:
        config, added = apply_starter_rules(config, body.ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = _persist_rules(config)
    result["added"] = added
    result["starter_rules"] = starter_rule_catalogue(load_rules())
    return result


_TOGGLEABLE_SECTIONS = {"automation", "digest", "snooze", "follow_ups", "learning"}


@app.post("/rules/rule-toggle")
async def toggle_rule(body: RuleToggleInput) -> dict:
    """Enable/disable a single named rule without editing YAML."""
    config = load_rules()
    rule = next((r for r in config.rules if r.name == body.name), None)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"No rule named {body.name!r}")
    rule.enabled = body.enabled
    return _persist_rules(config)


@app.post("/rules/section-toggle")
async def toggle_section(body: SectionToggleInput) -> dict:
    """Enable/disable a rules subsystem (automation master switch, digest, snooze,
    follow_ups, learning)."""
    if body.section not in _TOGGLEABLE_SECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown section {body.section!r}")
    config = load_rules()
    if body.section == "automation":
        config.enabled = body.enabled
    else:
        getattr(config, body.section).enabled = body.enabled
    return _persist_rules(config)


_CONFIGURABLE_SECTIONS = {"digest", "snooze", "follow_ups"}


@app.post("/rules/rule")
async def upsert_rule(body: RuleUpsertInput) -> dict:
    """Add or update one automation rule from a structured form (no YAML)."""
    from src.automation import AutomationRule

    config = load_rules()
    rule = AutomationRule(
        name=body.name, enabled=body.enabled, when=body.when, then=body.then
    )
    match = body.original_name or body.name
    if (body.original_name or body.name) != body.name and any(
        r.name == body.name for r in config.rules if r.name != match
    ):
        raise HTTPException(status_code=409, detail=f"A rule named {body.name!r} already exists")
    for index, existing in enumerate(config.rules):
        if existing.name == match:
            config.rules[index] = rule
            break
    else:
        config.rules.append(rule)
    return _persist_rules(config)


@app.post("/rules/rule-delete")
async def delete_rule(body: RuleDeleteInput) -> dict:
    # Rule names are free-text (spaces/unicode), so we take the name in a JSON body
    # rather than a URL path segment that the gateway firewall would reject encoded.
    config = load_rules()
    remaining = [r for r in config.rules if r.name != body.name]
    if len(remaining) == len(config.rules):
        raise HTTPException(status_code=404, detail=f"No rule named {body.name!r}")
    config.rules = remaining
    return _persist_rules(config)


@app.put("/rules/section-config")
async def update_section_config(body: SectionConfigInput) -> dict:
    """Replace a rules subsystem's config (digest/snooze/follow_ups) from a form,
    preserving its current enabled flag unless the form supplies one."""
    if body.section not in _CONFIGURABLE_SECTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown section {body.section!r}")
    config = load_rules()
    current = getattr(config, body.section)
    payload = {**current.model_dump(), **body.config}
    setattr(config, body.section, type(current)(**payload))
    return _persist_rules(config)


@app.get("/rules/suggestions")
async def get_rule_suggestions() -> dict:
    """Learned rule suggestions appended from human corrections (ignore/edit/feedback)."""
    suggestions = [
        {"index": idx, **item} for idx, item in enumerate(_read_suggestions())
    ]
    return {
        "suggestions": suggestions,
        "learning_enabled": load_rules().learning.enabled,
    }


@app.post("/rules/suggestions/{index}/promote")
async def promote_rule_suggestion(index: int) -> dict:
    """Promote a learned suggestion into an active rule or workflow."""
    valid = _read_suggestions()
    if index < 0 or index >= len(valid):
        raise HTTPException(status_code=404, detail="Suggestion not found")
    item = valid[index]
    remaining = [s for i, s in enumerate(valid) if i != index]

    if item.get("suggested_workflow"):
        result = _promote_workflow_suggestion(item)
        _write_suggestions(remaining)
        return result

    rule_dict = {**(item.get("suggested_rule") or {}), "enabled": True}
    rule = AutomationRule(**rule_dict)  # validate before persisting
    data = load_rules().model_dump()
    data["rules"].append(rule.model_dump())
    # Structured dump (comments are not preserved on promote — the YAML stays valid).
    write_instance_text(
        "rules", yaml.safe_dump(data, sort_keys=False), DEFAULT_RULES_PATH
    )
    _write_suggestions(remaining)
    return {"kind": "rule", "promoted": rule.model_dump(), "parsed": load_rules().model_dump()}


@app.delete("/rules/suggestions/{index}")
async def dismiss_rule_suggestion(index: int) -> dict:
    valid = _read_suggestions()
    if index < 0 or index >= len(valid):
        raise HTTPException(status_code=404, detail="Suggestion not found")
    remaining = [s for i, s in enumerate(valid) if i != index]
    _write_suggestions(remaining)
    return {"dismissed": index, "remaining": len(remaining)}


@app.get("/capabilities")
async def get_capabilities() -> dict:
    return {"capabilities": load_config().capabilities}


@app.put("/capabilities")
async def update_capabilities(body: CapabilitiesInput) -> dict:
    current = load_config().model_dump()
    current["capabilities"] = body.capabilities
    cfg = AgentConfig(**current)
    write_instance_text(
        "config", yaml.safe_dump(cfg.model_dump(), sort_keys=False), DEFAULT_CONFIG_PATH
    )
    reload_config()  # make the toggle live in this process (graph reads module globals)
    return {"capabilities": cfg.capabilities}


@app.get("/policy")
async def get_policy() -> dict:
    # The policy lives in the (separate) security service — fetch it over HTTP rather
    # than reaching for a file that isn't in this container. Parse it here (we already
    # have PyYAML) so the control panel can render a friendly table, not raw YAML.
    data = await fetch_policy()
    try:
        data["parsed"] = yaml.safe_load(data.get("policy_yaml") or "") or {}
    except yaml.YAMLError:
        data["parsed"] = {}
    return data


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
        print(f"api: persona suggestion Gmail read unavailable for user {user_id}: {exc}")
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
        print(f"api: persona suggestion analysis failed for user {user_id}: {exc}")
        raise HTTPException(status_code=503, detail="Persona analysis failed with the configured LLM") from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "suggestion": suggestion.model_dump(),
        "sent_sample_count": len(sent_samples),
        "received_sample_count": len(received),
    }


@app.get("/send-mode")
async def get_send_mode_endpoint() -> dict:
    return {
        "agent_instance_id": current_agent_instance_id(),
        "send_mode": get_send_mode(),
        "dry_run_lock": settings.dry_run,
        "effective_dry_run": effective_dry_run(),
    }


@app.put("/send-mode")
async def update_send_mode(request: Request, body: SendModeInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        mode = set_send_mode(body.send_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "send_mode": mode,
        "dry_run_lock": settings.dry_run,
        "effective_dry_run": effective_dry_run(),
    }


@app.get("/signature")
async def get_signature() -> dict:
    signature = load_signature()
    return {
        "agent_instance_id": current_agent_instance_id(),
        **signature.model_dump(),
        "available_modes": list(SIGNATURE_MODES),
    }


@app.put("/signature")
async def update_signature(body: SignatureConfig) -> dict:
    if body.mode not in SIGNATURE_MODES:
        raise HTTPException(status_code=422, detail=f"mode must be one of: {', '.join(SIGNATURE_MODES)}")
    save_signature(body)
    return {
        "agent_instance_id": current_agent_instance_id(),
        **body.model_dump(),
        "available_modes": list(SIGNATURE_MODES),
    }


class SignatureApplyInput(BaseModel):
    content: str
    mode: str


@app.post("/signature/apply")
async def apply_signature_endpoint(body: SignatureApplyInput) -> dict:
    """Compose the final body for one draft under an explicit mode override.

    Used by the approval UI's ask_each_time per-draft toggle: the chosen mode
    is applied here, and the already-signed content is then sent back through
    the normal 'edit' approval path — apply_signature's idempotency guarantee
    means the graph's own signature step (which runs with no override once
    signature.mode is ask_each_time) leaves this content untouched.
    """
    if body.mode not in SIGNATURE_MODES:
        raise HTTPException(status_code=422, detail=f"mode must be one of: {', '.join(SIGNATURE_MODES)}")
    signature = load_signature()
    return {"content": apply_signature(body.content, signature, mode=body.mode)}


@app.post("/signature/image")
async def upload_signature_image(request: Request, file: UploadFile = File(...)) -> dict:
    _require_instance_role(request, "owner")
    data = await file.read()
    try:
        stored = await asyncio.to_thread(save_signature_image, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "stored": True,
        "filename": stored.key.rsplit("/", 1)[-1],
        "size": stored.size,
    }


@app.get("/signature/image")
async def get_signature_image() -> Response:
    found = await asyncio.to_thread(signature_image_inline)
    if found is None:
        raise HTTPException(status_code=404, detail="No signature image for this instance")
    data, subtype = found
    return Response(content=data, media_type=f"image/{subtype}")


@app.delete("/signature/image")
async def remove_signature_image(request: Request) -> dict:
    _require_instance_role(request, "owner")
    removed = delete_signature_image()
    return {"agent_instance_id": current_agent_instance_id(), "removed": removed}


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
        print(f"api: style learning Gmail read unavailable for user {user_id}: {exc}")
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
        print(f"api: style learning analysis failed for user {user_id}: {exc}")
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
        all_runs = [r for r in all_runs if not r.get("workflow_dept") or r.get("workflow_dept") == user_dept]
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
                    if not r.get("workflow_dept") or r.get("workflow_dept") == user_dept
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
        print(f"api: gmail sync unavailable for user {user_id}: {error}")
        record_sync_failure(error)
        raise HTTPException(
            status_code=503,
            detail="Gmail sync is unavailable. Check OAuth credentials and container network access.",
        )

    try:
        effective_limit = limit or load_runtime_settings(current_agent_instance_id()).sync_limit
        outcomes = await poll_once(request.app.state.graph, provider=provider, max_results=effective_limit)
    except Exception as exc:
        print(f"api: gmail sync failed for user {user_id}: {exc}")
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
_INBOX_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_INBOX_CACHE_TTL_SECONDS = float(os.getenv("AGENT_INBOX_CACHE_TTL_SECONDS", "60"))
# How long an expired listing may still be shown while a fresh one is fetched.
# Past the TTL the entry is stale, not wrong: the messages are still the ones in
# the mailbox, only the "is there anything newer" answer has aged. Blocking the
# view on a live Gmail round trip to find out is what made opening Messages feel
# like the app had hung — and the poller invalidates this cache every cycle, so
# that round trip was landing on ordinary visits, not rare ones.
_INBOX_STALE_SECONDS = float(os.getenv("AGENT_INBOX_STALE_SECONDS", "900"))
_INBOX_CACHE_LOCK = threading.Lock()
_INBOX_REFRESHING: set[tuple] = set()


def _inbox_cache_key(key: tuple) -> str:
    """Redis key for a cache tuple: (user_id, agent_instance_id, mailbox, limit)."""
    return "agora:inbox:" + ":".join(str(part) for part in key)


def _inbox_cache_prefix(user_id: str | None, agent_instance_id: str | None) -> str:
    return f"agora:inbox:{user_id}:{agent_instance_id}:"


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


async def _inbox_unavailable(exc: Exception, user_id: str, user_dept: str | None, limit: int, mailbox: str = "inbox") -> dict:
    """Gmail is unreachable: fall back to the last runs this instance recorded."""
    print(f"api: gmail inbox unavailable for user {user_id}: {exc}")
    if mailbox == "sent":
        return {
            "messages": [],
            "warning": "Sent mail is unavailable. Check OAuth credentials and container network access.",
        }
    runs = await asyncio.to_thread(
        list_runs, user_id=None, agent_instance_id=current_agent_instance_id(), limit=500
    )
    if user_dept:
        runs = [r for r in runs if not r.get("workflow_dept") or r.get("workflow_dept") == user_dept]
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
        runs = [r for r in runs if not r.get("workflow_dept") or r.get("workflow_dept") == user_dept]
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
        print(f"api: gmail inbox action {action} unavailable for {msg_id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Gmail inbox is unavailable. Check OAuth credentials and container network access.",
        ) from exc
    # Archiving, trashing or flipping read state changes what the listing should
    # show, so the cached copy is dropped rather than left to expire.
    _inbox_cache_clear(current_user_id(), current_agent_instance_id())
    return {"ok": True, "msg_id": msg_id, "action": action}


@app.post("/inbox/{msg_id}/archive")
async def inbox_archive(msg_id: str) -> dict:
    return await _inbox_action("archive_message", msg_id, "archive")


@app.post("/inbox/{msg_id}/trash")
async def inbox_trash(msg_id: str) -> dict:
    return await _inbox_action("trash_message", msg_id, "trash")


@app.post("/inbox/{msg_id}/read")
async def inbox_read(msg_id: str) -> dict:
    return await _inbox_action("mark_as_read", msg_id, "read")


@app.post("/inbox/{msg_id}/unread")
async def inbox_unread(msg_id: str) -> dict:
    return await _inbox_action("mark_as_unread", msg_id, "unread")


@app.post("/inbox/{msg_id}/force-agent")
async def inbox_force_agent(request: Request, msg_id: str) -> dict:
    """Mark a message unread, clear its previous run, and process it immediately."""
    provider = get_provider()
    try:
        await asyncio.to_thread(provider.mark_as_unread, msg_id)
    except Exception as exc:
        print(f"api: gmail force-agent unavailable for {msg_id}: {exc}")
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


class AssignInput(BaseModel):
    assignee: str | None


@app.post("/inbox/{run_id}/claim")
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


@app.post("/inbox/{run_id}/assign")
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
            print(f"api: approve graph resume failed for run {run_id}; used pending action fallback: {exc}")
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
