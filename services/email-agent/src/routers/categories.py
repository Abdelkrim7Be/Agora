from __future__ import annotations

import yaml

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)
from pydantic import ValidationError
from src.categories import (
    CategoriesConfig,
    Category,
    CategoryInstructions,
    DEFAULT_CATEGORIES_PATH,
    classify_category,
    dump_categories,
)
from src.instance_config import write_instance_text
from src.tenant import current_agent_instance_id
from src.api_shared import (
    _current_categories,
    _require_instance_role,
)

import asyncio
import json
import re
from email.utils import parseaddr

from pydantic import BaseModel, Field

from src.config import SERVICE_ROOT
from src.instance_config import read_instance_text
from src.mail import get_provider

router = APIRouter()


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


DEFAULT_CATEGORY_PROPOSAL_STATE_PATH = SERVICE_ROOT / "logs" / "category_proposal_state.json"


class CategoriesInput(BaseModel):
    categories_yaml: str


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
    # Which tools this workflow may execute. None keeps the policy's own action
    # set; a list replaces it. Narrows only — it cannot grant a tool that
    # security/policy.yaml denies.
    allowed_actions: list[str] | None = None


class CategoryProposalActionInput(BaseModel):
    proposal_id: str
    name: str | None = None
    display_name: str | None = None


def _slugify_category(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "nouvelle_categorie"


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


class CategoryTestMatchInput(BaseModel):
    author: str = ""
    subject: str = ""
    email_thread: str = ""

@router.get("/categories")
async def get_categories() -> dict:
    categories_yaml, cfg = _current_categories()
    return {
        "agent_instance_id": current_agent_instance_id(),
        "categories_yaml": categories_yaml or dump_categories(cfg),
        "parsed": cfg.model_dump(),
        "storage": "instance-config",
    }


@router.put("/categories")
async def update_categories(body: CategoriesInput) -> dict:
    try:
        data = yaml.safe_load(body.categories_yaml) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"invalid categories YAML: {exc}") from exc
    data.setdefault("categories", [])
    data.setdefault("templates", [])
    data.setdefault("contacts", [])
    try:
        parsed = CategoriesConfig(**data)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"invalid categories config: {exc}") from exc
    write_instance_text("categories", body.categories_yaml, DEFAULT_CATEGORIES_PATH)
    return {
        "agent_instance_id": current_agent_instance_id(),
        "categories_yaml": body.categories_yaml,
        "parsed": parsed.model_dump(),
        "storage": "instance-config",
    }


@router.get("/categories/proposals")
async def category_proposals(limit: int = Query(default=500, ge=25, le=500)) -> dict:
    """Discover category proposals from recent Gmail metadata only."""
    return await _build_category_proposals(limit)


@router.post("/categories/proposals/accept")
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


@router.post("/categories/proposals/dismiss")
async def dismiss_category_proposal(body: CategoryProposalActionInput, request: Request) -> dict:
    _require_instance_role(request, "owner")
    dismissed = _dismissed_category_proposals()
    dismissed.add(body.proposal_id)
    _save_dismissed_category_proposals(dismissed)
    return {"dismissed": body.proposal_id}


@router.put("/categories/{name}")
async def update_category_endpoint(name: str, body: CategoryUpdateInput, request: Request) -> dict:
    _require_instance_role(request, "owner")
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
            cat.allowed_actions = body.allowed_actions
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


@router.delete("/categories/{name}")
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


@router.post("/categories/{name}/duplicate")
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


@router.post("/categories/test-match")
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
