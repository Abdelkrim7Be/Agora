"""Outbound broadcast campaigns: audience guard + segment-backed rendering.

Campaigns are owner-initiated, approval-gated broadcasts. Unlike inbound
categories, they never touch the triage graph — they render one personalized
email per audience member and surface a single batch approval. The contact
store is the source of truth for people; campaign groups are thin references to
segments so older UI paths keep working while contacts/segments become primary.
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from src.config import SERVICE_ROOT
from src.contacts import AUDIENCE_VALUES, Audience, Contact, get_segment, resolve_segment

DEFAULT_CAMPAIGNS_PATH = SERVICE_ROOT / "campaigns.yaml"

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class GroupMember(BaseModel):
    email: str = Field(min_length=3)
    name: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)

    @field_validator("email")
    @classmethod
    def _clean_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned:
            raise ValueError("member email must contain @")
        return cleaned


class Group(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: Literal["employees", "clients"] = "clients"
    segment_id: str | None = None
    members: list[GroupMember] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("group id must not be blank")
        return cleaned


class CampaignTemplate(BaseModel):
    name: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    body_markdown: str = Field(min_length=1)
    variables: list[str] = Field(default_factory=list)
    audience: list[Audience] = Field(default_factory=list)
    category: str | None = None

    @field_validator("variables")
    @classmethod
    def _clean_variables(cls, value: list[str]) -> list[str]:
        variables: list[str] = []
        seen: set[str] = set()
        for item in value or []:
            cleaned = str(item).strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            variables.append(cleaned)
        return variables

    @field_validator("audience")
    @classmethod
    def _clean_audience(cls, value: list[str]) -> list[str]:
        audiences: list[str] = []
        seen: set[str] = set()
        for item in value or []:
            cleaned = str(item).strip().lower()
            if not cleaned or cleaned in seen:
                continue
            if cleaned not in AUDIENCE_VALUES:
                raise ValueError(f"audience must be one of: {', '.join(AUDIENCE_VALUES)}")
            seen.add(cleaned)
            audiences.append(cleaned)
        if not audiences:
            raise ValueError("audience must contain at least one allowed audience")
        return audiences

    @field_validator("category")
    @classmethod
    def _clean_category(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None


class CampaignsConfig(BaseModel):
    groups: list[Group] = Field(default_factory=list)
    templates: list[CampaignTemplate] = Field(default_factory=list)


def _normalized_group(raw: dict) -> Group:
    payload = dict(raw or {})
    payload.setdefault("members", [])
    payload.setdefault("segment_id", payload.get("id"))
    return Group(**payload)


def load_campaigns(path: str | Path | None = None) -> CampaignsConfig:
    campaigns_path = Path(path) if path else DEFAULT_CAMPAIGNS_PATH
    if not campaigns_path.is_file():
        return CampaignsConfig()
    data = yaml.safe_load(campaigns_path.read_text()) or {}
    groups = [_normalized_group(item) for item in data.get("groups", [])]
    templates = [CampaignTemplate(**item) for item in data.get("templates", [])]
    return CampaignsConfig(groups=groups, templates=templates)


def dump_campaigns(config: CampaignsConfig) -> str:
    payload = {
        "groups": [
            {
                "id": group.id,
                "name": group.name,
                "type": group.type,
                "segment_id": group.segment_id or group.id,
            }
            for group in config.groups
        ],
        "templates": [template.model_dump() for template in config.templates],
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def save_campaigns(config: CampaignsConfig, path: str | Path | None = None) -> None:
    campaigns_path = Path(path) if path else DEFAULT_CAMPAIGNS_PATH
    campaigns_path.write_text(dump_campaigns(config))


def find_group(config: CampaignsConfig, group_id: str) -> Group | None:
    return next((g for g in config.groups if g.id == group_id), None)


def find_template(config: CampaignsConfig, name: str) -> CampaignTemplate | None:
    return next((t for t in config.templates if t.name == name), None)


def unresolved_vars(text: str) -> list[str]:
    return _VAR_RE.findall(text)


def _member_values(member: GroupMember) -> dict[str, str]:
    values: dict[str, str] = {"email": member.email}
    if member.name and member.name.strip():
        values["name"] = member.name.strip()
        values["prenom"] = member.name.strip().split()[0]
    for key, value in (member.fields or {}).items():
        values[str(key)] = str(value)
    return values


def render_text(text: str, values: dict[str, str]) -> str:
    def _sub(match: re.Match) -> str:
        key = match.group(1)
        return str(values.get(key, match.group(0)))

    return _VAR_RE.sub(_sub, text)


def _markdown_to_html(md_text: str) -> str:
    try:
        import markdown as _md

        return _md.markdown(md_text, extensions=["extra", "sane_lists", "nl2br"])
    except Exception:  # pragma: no cover
        paragraphs = [p.strip() for p in md_text.split("\n\n") if p.strip()]
        return "".join(
            "<p>" + _html.escape(p).replace("\n", "<br>") + "</p>" for p in paragraphs
        )


_HTML_SHELL = (
    '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
    'font-size:15px;line-height:1.6;color:#1a1a1a;max-width:640px;margin:0 auto;">'
    "{body}"
    "</div>"
)


class RenderedEmail(BaseModel):
    email: str
    name: str | None = None
    subject: str
    html: str
    text: str
    unresolved: list[str] = Field(default_factory=list)


class MissingVariableWarning(BaseModel):
    email: str
    name: str | None = None
    unresolved: list[str] = Field(default_factory=list)


class AudienceGuardResult(BaseModel):
    allowed: bool
    segment_audiences: list[Audience] = Field(default_factory=list)
    template_audience: list[Audience] = Field(default_factory=list)
    reason: str | None = None


def _member_from_contact(contact: Contact) -> GroupMember:
    return GroupMember(email=contact.email, name=contact.name, fields=contact.fields)


def contacts_for_segment(segment_id: str, agent_instance_id: str | None = None) -> list[Contact]:
    segment = get_segment(segment_id, agent_instance_id=agent_instance_id)
    if segment is None:
        return []
    return resolve_segment(segment, agent_instance_id=agent_instance_id)


def members_for_group(group: Group, agent_instance_id: str | None = None) -> list[GroupMember]:
    if group.members:
        return group.members
    contacts = contacts_for_segment(group.segment_id or group.id, agent_instance_id=agent_instance_id)
    return [_member_from_contact(contact) for contact in contacts]


def render_for_member(template: CampaignTemplate, member: GroupMember) -> RenderedEmail:
    values = _member_values(member)
    subject = render_text(template.subject, values)
    body_md = render_text(template.body_markdown, values)
    html_body = _HTML_SHELL.format(body=_markdown_to_html(body_md))
    unresolved = sorted(set(unresolved_vars(subject) + unresolved_vars(body_md)))
    return RenderedEmail(
        email=member.email,
        name=member.name,
        subject=subject,
        html=html_body,
        text=body_md,
        unresolved=unresolved,
    )


def render_campaign(group: Group, template: CampaignTemplate, agent_instance_id: str | None = None) -> list[RenderedEmail]:
    return [render_for_member(template, member) for member in members_for_group(group, agent_instance_id=agent_instance_id)]


def render_campaign_for_segment(segment_id: str, template: CampaignTemplate, agent_instance_id: str | None = None) -> list[RenderedEmail]:
    contacts = contacts_for_segment(segment_id, agent_instance_id=agent_instance_id)
    return [render_for_member(template, _member_from_contact(contact)) for contact in contacts]


def audience_guard(template: CampaignTemplate, contacts: list[Contact], segment_name: str | None = None) -> AudienceGuardResult:
    template_audience = sorted(set(template.audience))
    segment_audiences = sorted({contact.audience for contact in contacts})
    if not template_audience or not segment_audiences or not set(template_audience).isdisjoint(segment_audiences):
        return AudienceGuardResult(
            allowed=True,
            segment_audiences=segment_audiences,
            template_audience=template_audience,
        )

    expected = ", ".join(template_audience)
    actual = ", ".join(segment_audiences)
    target = f"le segment « {segment_name} »" if segment_name else "ce segment"
    return AudienceGuardResult(
        allowed=False,
        segment_audiences=segment_audiences,
        template_audience=template_audience,
        reason=(
            f"Audience incompatible : le modèle « {template.name} » cible {expected} "
            f"alors que {target} contient {actual}."
        ),
    )


def missing_variable_warnings(rendered: list[RenderedEmail]) -> list[MissingVariableWarning]:
    return [
        MissingVariableWarning(email=item.email, name=item.name, unresolved=item.unresolved)
        for item in rendered
        if item.unresolved
    ]
