from __future__ import annotations

import asyncio
import yaml

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)
from src.alerts import (
    AlertSettings,
    load_alert_settings,
    save_alert_settings,
)
from src.config import (
    AgentConfig,
    DEFAULT_CONFIG_PATH,
    load_config,
    settings,
)
from src.graph import reload_config
from src.instance_config import write_instance_text
from src.junk_config import (
    JunkConfig,
    load_junk,
    save_junk,
    suggest_junk_senders,
)
from src.mail import get_provider
from src.retention import (
    RetentionSettings,
    load_retention_settings,
    preview_retention,
    run_retention,
    save_retention_settings,
)
from src.runtime_settings import (
    RuntimeSettings,
    load_runtime_settings,
    save_runtime_settings,
)
from src.security_client import fetch_policy
from src.send_mode import (
    effective_dry_run,
    get_send_mode,
    set_send_mode,
)
from src.sensitivity_config import (
    SensitivityConfig,
    load_sensitivity,
    save_sensitivity,
)
from src.tenant import current_agent_instance_id
from src.api_shared import (
    _require_instance_role,
)

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


class SendModeInput(BaseModel):
    send_mode: str


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


class SensitivityInput(BaseModel):
    """Known-sensitive mail settings edited from the UI (metadata-only gate)."""

    enabled: bool | None = None
    allowed_senders: list[str] | None = None
    allowed_domains: list[str] | None = None
    blocked_senders: list[str] | None = None
    blocked_domains: list[str] | None = None
    subject_keywords: list[str] | None = None


class CapabilitiesInput(BaseModel):
    capabilities: dict[str, bool]


class RuntimeSettingsInput(BaseModel):
    sync_limit: int = Field(ge=1, le=500)
    setup_recent_limit: int = Field(ge=1, le=500)
    setup_backlog_limit: int = Field(ge=1, le=500)
    setup_sent_sample: int = Field(ge=1, le=500)
    # Fed whole into a single style-learning prompt, unlike the fields above (one call
    # per message) — a small local model's context window caps this well below 500.
    style_sent_sample: int = Field(ge=1, le=50)

@router.get("/alerts/settings")
async def alerts_settings(request: Request) -> dict:
    _require_instance_role(request, "owner")
    return load_alert_settings().model_dump()


@router.put("/alerts/settings")
async def update_alerts_settings(request: Request, body: AlertSettings) -> dict:
    _require_instance_role(request, "owner")
    return save_alert_settings(body).model_dump()


@router.get("/retention/settings")
async def retention_settings(request: Request) -> dict:
    _require_instance_role(request, "owner")
    return load_retention_settings().model_dump()


@router.put("/retention/settings")
async def update_retention_settings(request: Request, body: RetentionSettings) -> dict:
    _require_instance_role(request, "owner")
    return save_retention_settings(body).model_dump()


@router.get("/runtime-settings")
async def runtime_settings(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    return load_runtime_settings(current_agent_instance_id()).model_dump()


@router.put("/runtime-settings")
async def update_runtime_settings(request: Request, body: RuntimeSettingsInput) -> dict:
    _require_instance_role(request, "owner")
    config = RuntimeSettings(**body.model_dump())
    return save_runtime_settings(config, current_agent_instance_id()).model_dump()


@router.post("/retention/dry-run")
async def retention_dry_run(request: Request) -> dict:
    _require_instance_role(request, "owner")
    return await asyncio.to_thread(preview_retention)


@router.post("/retention/run")
async def retention_execute(request: Request) -> dict:
    _require_instance_role(request, "owner")
    retention_config = await asyncio.to_thread(load_retention_settings)
    if retention_config.retention_days <= 0:
        raise HTTPException(status_code=400, detail="retention disabled; set retention_days > 0 before executing")
    return await asyncio.to_thread(run_retention)


@router.get("/junk/suggestions")
async def junk_suggestions(request: Request, limit: int = Query(default=200, ge=25, le=500)) -> dict:
    """Block candidates taken from this mailbox's own traffic, not placeholders."""
    _require_instance_role(request, "viewer")
    config = load_junk(agent_instance_id=current_agent_instance_id())
    try:
        messages = await asyncio.to_thread(get_provider().list_inbox, limit)
    except Exception as exc:
        logger.warning(f"api: junk suggestions unavailable: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Gmail inbox is unavailable. Check OAuth credentials and container network access.",
        ) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "scanned": len(messages),
        "suggestions": suggest_junk_senders(messages, config),
    }


@router.get("/junk")
async def get_junk(request: Request) -> dict:
    """Junk-gate settings for this instance, plus the reasons the gate can report."""
    _require_instance_role(request, "viewer")
    config = load_junk(agent_instance_id=current_agent_instance_id())
    return {
        "agent_instance_id": current_agent_instance_id(),
        "junk": config.model_dump(),
    }


@router.put("/junk")
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


@router.get("/sensitivity")
async def get_sensitivity(request: Request) -> dict:
    """Sensitivity-gate settings for this instance."""
    _require_instance_role(request, "viewer")
    config = load_sensitivity(agent_instance_id=current_agent_instance_id())
    return {
        "agent_instance_id": current_agent_instance_id(),
        "sensitivity": config.model_dump(),
    }


@router.put("/sensitivity")
async def update_sensitivity(request: Request, body: SensitivityInput) -> dict:
    """Patch sensitivity-gate settings; omitted fields keep their current value."""
    _require_instance_role(request, "owner")
    current = load_sensitivity(agent_instance_id=current_agent_instance_id())
    patch = body.model_dump(exclude_none=True)
    for key in ("allowed_senders", "allowed_domains", "blocked_senders", "blocked_domains", "subject_keywords"):
        if key in patch:
            patch[key] = [item.strip().lower() for item in patch[key] if item and item.strip()]
    updated = SensitivityConfig(**{**current.model_dump(), **patch})
    save_sensitivity(updated, agent_instance_id=current_agent_instance_id())
    return {
        "agent_instance_id": current_agent_instance_id(),
        "sensitivity": updated.model_dump(),
    }


@router.get("/capabilities")
async def get_capabilities() -> dict:
    return {"capabilities": load_config().capabilities}


@router.put("/capabilities")
async def update_capabilities(body: CapabilitiesInput) -> dict:
    current = load_config().model_dump()
    current["capabilities"] = body.capabilities
    cfg = AgentConfig(**current)
    write_instance_text(
        "config", yaml.safe_dump(cfg.model_dump(), sort_keys=False), DEFAULT_CONFIG_PATH
    )
    reload_config()  # make the toggle live in this process (graph reads module globals)
    return {"capabilities": cfg.capabilities}


@router.get("/policy")
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


@router.get("/send-mode")
async def get_send_mode_endpoint() -> dict:
    return {
        "agent_instance_id": current_agent_instance_id(),
        "send_mode": get_send_mode(),
        "dry_run_lock": settings.dry_run,
        "effective_dry_run": effective_dry_run(),
    }


@router.put("/send-mode")
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
