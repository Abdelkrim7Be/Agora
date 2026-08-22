from __future__ import annotations

import yaml

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from pydantic import ValidationError
from src.automation import (
    AutomationRule,
    DEFAULT_RULES_PATH,
    RuleWhen,
    RulesConfig,
    apply_starter_rules,
    load_rules,
    starter_rule_catalogue,
)
from src.instance_config import (
    read_instance_text,
    write_instance_text,
)
from src.api_shared import (
    _require_instance_role,
)

import json

from pydantic import BaseModel, Field

from src.categories import DEFAULT_CATEGORIES_PATH, dump_categories
from src.config import SERVICE_ROOT
from src.api_shared import _current_categories

router = APIRouter()


def _suggestions_path():
    rel = load_rules().learning.suggestions_path or "logs/rule_suggestions.jsonl"
    return SERVICE_ROOT / rel


def _merge_rule_when(existing, learned_when: dict | None):
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


class RulesInput(BaseModel):
    rules_yaml: str


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


_TOGGLEABLE_SECTIONS = {"automation", "digest", "snooze", "follow_ups", "learning"}


_CONFIGURABLE_SECTIONS = {"digest", "snooze", "follow_ups"}

@router.get("/rules")
async def get_rules() -> dict:
    rules_yaml = read_instance_text("rules", DEFAULT_RULES_PATH) or "enabled: false\n"
    return {
        "rules_yaml": rules_yaml,
        "parsed": load_rules().model_dump(),
    }


@router.put("/rules")
async def update_rules(body: RulesInput) -> dict:
    try:
        data = yaml.safe_load(body.rules_yaml) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"invalid rules YAML: {exc}") from exc
    if data.get("rules") is None:
        data["rules"] = []
    try:
        parsed = RulesConfig(**data)  # validate before persisting
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"invalid rules config: {exc}") from exc
    # Persist the user's raw YAML verbatim so comments/formatting survive a round-trip.
    write_instance_text("rules", body.rules_yaml, DEFAULT_RULES_PATH)
    return {
        "rules_yaml": body.rules_yaml,
        "parsed": parsed.model_dump(),
    }


@router.get("/rules/starter")
async def get_starter_rules() -> dict:
    """Sensible starter rules for a small company, offered rather than forced."""
    return {"starter_rules": starter_rule_catalogue(load_rules())}


@router.post("/rules/starter/apply")
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


@router.post("/rules/rule-toggle")
async def toggle_rule(body: RuleToggleInput) -> dict:
    """Enable/disable a single named rule without editing YAML."""
    config = load_rules()
    rule = next((r for r in config.rules if r.name == body.name), None)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"No rule named {body.name!r}")
    rule.enabled = body.enabled
    return _persist_rules(config)


@router.post("/rules/section-toggle")
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


@router.post("/rules/rule")
async def upsert_rule(body: RuleUpsertInput) -> dict:
    """Add or update one automation rule from a structured form (no YAML)."""
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


@router.post("/rules/rule-delete")
async def delete_rule(body: RuleDeleteInput) -> dict:
    # Rule names are free-text (spaces/unicode), so we take the name in a JSON body
    # rather than a URL path segment that the gateway firewall would reject encoded.
    config = load_rules()
    remaining = [r for r in config.rules if r.name != body.name]
    if len(remaining) == len(config.rules):
        raise HTTPException(status_code=404, detail=f"No rule named {body.name!r}")
    config.rules = remaining
    return _persist_rules(config)


@router.put("/rules/section-config")
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


@router.get("/rules/suggestions")
async def get_rule_suggestions() -> dict:
    """Learned rule suggestions appended from human corrections (ignore/edit/feedback)."""
    suggestions = [
        {"index": idx, **item} for idx, item in enumerate(_read_suggestions())
    ]
    return {
        "suggestions": suggestions,
        "learning_enabled": load_rules().learning.enabled,
    }


@router.post("/rules/suggestions/{index}/promote")
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


@router.delete("/rules/suggestions/{index}")
async def dismiss_rule_suggestion(index: int) -> dict:
    valid = _read_suggestions()
    if index < 0 or index >= len(valid):
        raise HTTPException(status_code=404, detail="Suggestion not found")
    remaining = [s for i, s in enumerate(valid) if i != index]
    _write_suggestions(remaining)
    return {"dismissed": index, "remaining": len(remaining)}
