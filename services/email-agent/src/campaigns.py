"""Outbound broadcast campaigns: group directory + rich templates + rendering.

Campaigns are owner-initiated, approval-gated broadcasts. Unlike inbound
categories, they never touch the triage graph — they render one personalized
email per group member and surface a single batch approval. Storage mirrors the
categories.yaml pattern: a flat, Postgres-ready YAML at the service root.
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from src.config import SERVICE_ROOT

DEFAULT_CAMPAIGNS_PATH = SERVICE_ROOT / "campaigns.yaml"

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class GroupMember(BaseModel):
    email: str = Field(min_length=3)
    name: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)

    @field_validator("email")
    @classmethod
    def _clean_email(cls, value: str) -> str:
        cleaned = value.strip()
        if "@" not in cleaned:
            raise ValueError("member email must contain @")
        return cleaned


class Group(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: Literal["employees", "clients"] = "clients"
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


class CampaignsConfig(BaseModel):
    groups: list[Group] = Field(default_factory=list)
    templates: list[CampaignTemplate] = Field(default_factory=list)


def load_campaigns(path: str | Path | None = None) -> CampaignsConfig:
    campaigns_path = Path(path) if path else DEFAULT_CAMPAIGNS_PATH
    if not campaigns_path.is_file():
        return CampaignsConfig()
    data = yaml.safe_load(campaigns_path.read_text()) or {}
    data.setdefault("groups", [])
    data.setdefault("templates", [])
    return CampaignsConfig(**data)


def dump_campaigns(config: CampaignsConfig) -> str:
    return yaml.safe_dump(config.model_dump(), sort_keys=False, allow_unicode=True)


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
    """Placeholder values available to a template for one recipient."""
    values: dict[str, str] = {"email": member.email}
    if member.name and member.name.strip():
        values["name"] = member.name.strip()
        values["prenom"] = member.name.strip().split()[0]
    # Per-member custom fields (amount, dept, company, ...) win over defaults but
    # never override the reserved keys above by accident — explicit fields do.
    for key, value in (member.fields or {}).items():
        values[str(key)] = str(value)
    return values


def render_text(text: str, values: dict[str, str]) -> str:
    def _sub(match: re.Match) -> str:
        key = match.group(1)
        return str(values.get(key, match.group(0)))

    return _VAR_RE.sub(_sub, text)


def _markdown_to_html(md_text: str) -> str:
    """Render a markdown body to a self-contained HTML fragment.

    Uses the `markdown` lib when available; falls back to a minimal converter so
    a missing dependency never breaks a render (defensive — the dep is declared).
    """
    try:
        import markdown as _md  # local import: optional at import time

        return _md.markdown(md_text, extensions=["extra", "sane_lists", "nl2br"])
    except Exception:  # pragma: no cover - fallback path
        paragraphs = [p.strip() for p in md_text.split("\n\n") if p.strip()]
        return "".join(
            "<p>" + _html.escape(p).replace("\n", "<br>") + "</p>" for p in paragraphs
        )


# A neutral, email-client-safe wrapper. Inline styles only (Gmail strips <style>).
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


def render_campaign(group: Group, template: CampaignTemplate) -> list[RenderedEmail]:
    return [render_for_member(template, m) for m in group.members]
