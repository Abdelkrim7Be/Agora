from __future__ import annotations

import uuid

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from pydantic import (
    BaseModel,
    Field,
)
from src.campaigns import (
    CampaignTemplate,
    Group,
    GroupMember,
    find_group,
    load_campaign_runs,
    load_campaigns,
    save_campaign_runs,
    save_campaigns,
    send_campaign_run,
    upsert_campaign_run,
)
from src.contacts import (
    Contact,
    Segment,
    SegmentNotFoundError,
    delete_segment,
    get_contact,
    get_segment,
    upsert_contact,
    upsert_segment,
)
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
)
from src.api_shared import (
    _require_instance_role,
)

from src.campaigns import (
    CampaignsConfig,
    audience_guard,
    contacts_for_segment,
    find_template,
    members_for_group,
    missing_variable_warnings,
    render_campaign_for_segment,
)
from src.api_shared import _now_iso

router = APIRouter()


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


def _serialize_group(group: Group) -> dict:
    members = members_for_group(group, agent_instance_id=current_agent_instance_id())
    return group.model_dump() | {
        "segment_id": group.segment_id or group.id,
        "members": [member.model_dump() for member in members],
        "member_count": len(members),
    }


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


def _campaign_records_for_current_instance() -> dict[str, dict]:
    instance = current_agent_instance_id()
    return {
        cid: rec
        for cid, rec in load_campaign_runs(agent_instance_id=instance).items()
        if rec.get("agent_instance_id") == instance
    }

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


@router.get("/campaigns/groups")
async def list_groups() -> dict:
    cfg = load_campaigns()
    return {
        "agent_instance_id": current_agent_instance_id(),
        "groups": [_serialize_group(group) for group in cfg.groups],
    }


@router.post("/campaigns/groups")
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


@router.delete("/campaigns/groups/{group_id}")
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


@router.get("/campaigns/templates")
async def list_campaign_templates() -> dict:
    cfg = load_campaigns()
    return {"templates": [t.model_dump() for t in cfg.templates]}


@router.post("/campaigns/templates")
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


@router.delete("/campaigns/templates/{name}")
async def delete_campaign_template(request: Request, name: str) -> dict:
    _require_instance_role(request, "owner")
    cfg = load_campaigns()
    before = len(cfg.templates)
    cfg.templates = [t for t in cfg.templates if t.name != name]
    if len(cfg.templates) == before:
        raise HTTPException(status_code=404, detail="template not found")
    save_campaigns(cfg)
    return {"deleted": name}


@router.get("/campaigns")
async def list_campaigns() -> dict:
    instance = current_agent_instance_id()
    items = [
        _campaign_summary(cid, rec)
        for cid, rec in _campaign_records_for_current_instance().items()
    ]
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return {"agent_instance_id": instance, "campaigns": items}


@router.post("/campaigns/preview")
async def preview_campaign(request: Request, body: CampaignPrepareInput) -> dict:
    _require_instance_role(request, "owner")
    cfg = load_campaigns()
    preview = _campaign_preview_payload(cfg, body)
    preview.pop("rendered", None)
    return preview


@router.post("/campaigns/prepare")
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


@router.post("/campaigns/{campaign_id}/reject")
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


@router.post("/campaigns/{campaign_id}/approve")
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
