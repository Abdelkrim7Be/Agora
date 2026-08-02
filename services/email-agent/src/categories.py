from __future__ import annotations

import re
from email.utils import parseaddr
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from src.automation import RuleWhen
from src.config import SERVICE_ROOT

DEFAULT_CATEGORIES_PATH = SERVICE_ROOT / "categories.yaml"


class Template(BaseModel):
    name: str = Field(min_length=1)
    subject: str | None = None
    body: str = Field(min_length=1)
    variables: list[str] = Field(default_factory=list)


class CategoryInstructions(BaseModel):
    sla: str | None = None
    required_data: list[str] = Field(default_factory=list)
    escalation: str | None = None
    blocked_cases: list[str] = Field(default_factory=list)
    ask_for_missing: str | bool | None = None


class Category(BaseModel):
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    description: str | None = None
    # Whether this category may claim mail the junk gate flagged as automated or
    # bulk. Off by default: a category matching on a sender must not turn that
    # sender's newsletters and alert digests into drafted replies. Turn it on for
    # workflows whose input genuinely is machine-generated — invoices emitted by
    # a billing system, ticket notifications from a helpdesk.
    accepts_automated: bool = False
    # How the category's template is used when the policy is auto_draft.
    #   strict — send the rendered template as-is when every variable resolved.
    #            Deterministic and free, right for a pure acknowledgement.
    #   adapt  — always hand the template to the model as a starting point, with
    #            the message in front of it. Costs a model call, but the reply
    #            answers what was actually written instead of asking for details
    #            the sender already gave.
    template_mode: Literal["strict", "adapt"] = "strict"
    enabled: bool = True
    priority: Literal["urgent", "normal", "low"] = "normal"
    when: RuleWhen = Field(default_factory=RuleWhen)
    template: str | None = None
    policy: Literal["auto_draft", "notify", "organize", "ignore"] = "notify"
    labels: list[str] = Field(default_factory=list)
    owner: str | None = None
    approver: str | None = None
    route_to: list[str] = Field(default_factory=list)
    instructions: CategoryInstructions | None = None
    # Per-workflow approval policy — layered on top of the tool-level default in
    # security/policy.yaml, never looser than it. require_approval can only
    # escalate allow -> hitl; it can never downgrade an existing hitl/deny to
    # allow. external_send_allowed=False restricts this category's send-style
    # tool calls to AGENT_INTERNAL_DOMAINS recipients only.
    require_approval: bool = False
    external_send_allowed: bool = True

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("category name must not be blank")
        return cleaned


class Contact(BaseModel):
    email: str | None = None        # exact sender address match
    domain: str | None = None       # whole sender domain match (used when email is absent)
    name: str | None = None         # fills {{name}} / {{prenom}} in templates
    category: str | None = None
    display_name: str | None = None
    priority: Literal["urgent", "normal", "low"] | None = None


class CategoriesConfig(BaseModel):
    enabled: bool = False
    categories: list[Category] = Field(default_factory=list)
    templates: list[Template] = Field(default_factory=list)
    contacts: list[Contact] = Field(default_factory=list)


def load_categories(path: str | Path | None = None, agent_instance_id: str | None = None) -> CategoriesConfig:
    categories_path = Path(path) if path else DEFAULT_CATEGORIES_PATH
    if path is None:
        from src.instance_config import read_instance_text

        raw = read_instance_text("categories", categories_path, agent_instance_id)
    elif categories_path.is_file():
        raw = categories_path.read_text()
    else:
        raw = ""
    data = yaml.safe_load(raw) or {}
    data.setdefault("categories", [])
    data.setdefault("templates", [])
    data.setdefault("contacts", [])
    return CategoriesConfig(**data)


def dump_categories(config: CategoriesConfig) -> str:
    return yaml.safe_dump(config.model_dump(), sort_keys=False)


def save_categories(config: CategoriesConfig, path: str | Path | None = None) -> None:
    categories_path = Path(path) if path else DEFAULT_CATEGORIES_PATH
    categories_path.write_text(dump_categories(config))


def _contains_any(haystack: str, needles: list[str]) -> bool:
    text = haystack.lower()
    return any(needle.lower() in text for needle in needles)


def _sender_domain(sender: str) -> str:
    _name, address = parseaddr(sender)
    if "@" not in address:
        return ""
    return address.rsplit("@", 1)[1].lower()


def _email_address(sender: str) -> str:
    _name, address = parseaddr(sender)
    return (address or sender).strip().lower()


def matches_when(when: RuleWhen, email_input: dict) -> bool:
    """True when all non-empty conditions on `when` match.

    A RuleWhen with no conditions set at all never matches here — an empty
    predicate is only meaningful as a catch-all in automation rules, not for
    category classification where it would incorrectly catch every email.
    """
    if not when.sender_contains and not when.sender_regex and not when.sender_domain and not when.subject_contains and not when.body_contains and not when.labels:
        return False
    sender = email_input.get("author", "")
    subject = email_input.get("subject", "")
    body = email_input.get("email_thread", "")
    labels = set(email_input.get("labels", []))
    if when.sender_contains and not _contains_any(sender, when.sender_contains):
        return False
    if when.sender_regex:
        try:
            if not any(re.search(pattern, sender, re.IGNORECASE) for pattern in when.sender_regex):
                return False
        except re.error:
            return False
    if when.sender_domain and _sender_domain(sender) not in {d.lower() for d in when.sender_domain}:
        return False
    if when.subject_contains and not _contains_any(subject, when.subject_contains):
        return False
    if when.body_contains and not _contains_any(body, when.body_contains):
        return False
    if when.labels and not set(when.labels).issubset(labels):
        return False
    return True


def unresolved_vars(text: str) -> list[str]:
    """Return any unresolved {{var}} placeholders remaining in a rendered template."""
    return re.findall(r"\{\{(\w+)\}\}", text)


def contact_directory_for_matching(config: CategoriesConfig, agent_instance_id: str | None = None) -> tuple[list, list]:
    """(directory_contacts, legacy_contacts) in classify_category's precedence order.

    directory_contacts are src.contacts unified-directory entries that carry a
    category (audience/fields/tags contacts without one are irrelevant to
    routing). legacy_contacts are the categories.yaml contacts NOT already
    present in the directory by email — existing categories.yaml files keep
    matching unchanged; new writes go to the directory. Directory contacts
    always have an email (schema-required), so only legacy contacts can ever
    satisfy the domain-only match branch below.
    """
    from src.contacts import list_contacts

    directory_contacts = [c for c in list_contacts(agent_instance_id=agent_instance_id) if c.category]
    directory_emails = {c.email for c in directory_contacts}
    legacy_contacts = [
        c for c in config.contacts
        if not (c.email and _email_address(c.email) in directory_emails)
    ]
    return directory_contacts, legacy_contacts


def classify_category(email_input: dict, config: CategoriesConfig, agent_instance_id: str | None = None) -> dict:
    if not config.enabled:
        return {"category": None, "priority": "normal", "template": None, "policy": None, "contact": None}

    sender_address = _email_address(email_input.get("author", ""))
    sender_domain = _sender_domain(email_input.get("author", ""))
    active_categories = [category for category in config.categories if category.enabled]
    category_by_name = {category.name: category for category in active_categories}

    directory_contacts, legacy_contacts = contact_directory_for_matching(config, agent_instance_id)

    def _result(contact, category) -> dict:
        return {
            "category": category.name,
            "category_display_name": category.display_name,
            "priority": (contact.priority if contact.priority else None) or category.priority,
            "template": category.template,
            "policy": category.policy,
            "owner": category.owner,
            "approver": category.approver,
            "route_to": category.route_to,
            "instructions": category.instructions.model_dump(exclude_none=True) if category.instructions else None,
            "contact": contact,
        }

    # The contact whose category applies to this sender, if any. Looked up first
    # but applied last: it says who wrote, not what they wrote about.
    matched_contact = None
    for contact in (*directory_contacts, *legacy_contacts):
        if contact.email and _email_address(contact.email) == sender_address and contact.category in category_by_name:
            matched_contact = contact
            break
    if matched_contact is None:
        # Domain match when email is unset, directory before legacy (directory
        # contacts never reach here — see contact_directory_for_matching).
        for contact in (*directory_contacts, *legacy_contacts):
            if (
                not contact.email
                and contact.domain
                and contact.domain.lower() == sender_domain
                and contact.category in category_by_name
            ):
                matched_contact = contact
                break

    # What the message is about beats who sent it. The sender's category used to
    # win outright, so a single directory entry filed every message from that
    # address under one category — a complaint, an internship application and an
    # invoice reminder all landed in the same workflow, and the subject rules the
    # owner had written were never consulted. A contact category is the fallback
    # for mail no rule claims, and still supplies the priority override.
    for category in active_categories:
        if matches_when(category.when, email_input):
            if matched_contact is not None:
                return {
                    **_result(matched_contact, category),
                    "category": category.name,
                    "category_display_name": category.display_name,
                }
            return {
                "category": category.name,
                "category_display_name": category.display_name,
                "priority": category.priority,
                "template": category.template,
                "policy": category.policy,
                "owner": category.owner,
                "approver": category.approver,
                "route_to": category.route_to,
                "instructions": category.instructions.model_dump(exclude_none=True) if category.instructions else None,
                "contact": None,
            }

    if matched_contact is not None:
        return _result(matched_contact, category_by_name[matched_contact.category])

    return {"category": None, "priority": "normal", "template": None, "policy": None, "contact": None}


def _re_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def render_template_text(text: str, email_input: dict, contact: "Contact | None" = None) -> str:
    sender_name, _sender_address = parseaddr(email_input.get("author", ""))
    sender_name = " ".join(sender_name.split())
    values = {
        "subject": email_input.get("subject", ""),
        "author": email_input.get("author", ""),
        "to": email_input.get("to", ""),
        "email_thread": email_input.get("email_thread", ""),
        "name": sender_name,
        "prenom": sender_name.split()[0] if sender_name else "",
    }
    if contact and contact.name and contact.name.split():
        values["name"] = contact.name
        values["prenom"] = contact.name.split()[0]
    rendered = text
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    # Unknown or empty variables must never reach a draft surface. Remove the
    # placeholder, then clean punctuation it would have owned.
    rendered = re.sub(r"\{\{\s*\w+\s*\}\}", "", rendered)
    rendered = re.sub(r"[ \t]+([,.;])", r"\1", rendered)
    rendered = re.sub(r"([,.;:!?]){2,}", r"\1", rendered)
    rendered = re.sub(r"[ \t]{2,}", " ", rendered)
    rendered = re.sub(r"(?m)^[ \t]*[,.;:!?]+[ \t]*$", "", rendered)
    rendered = re.sub(r"(?m)^Bonjour[ \t]*$", "Bonjour,", rendered)
    rendered = re.sub(r"(?m)^Bonjour[ \t]*[,;:][ \t]*$", "Bonjour,", rendered)
    rendered = re.sub(r"(?m)^(.+?)[ \t]+[,;][ \t]*$", r"\1,", rendered)
    return rendered.strip()


def auto_draft_tool_call(
    email_input: dict,
    config: CategoriesConfig,
    category_name: str | None,
    contact: "Contact | None" = None,
) -> dict | None:
    if not category_name:
        return None
    category = next((item for item in config.categories if item.name == category_name), None)
    if category is None or category.policy != "auto_draft" or not category.template:
        return None
    template = next((item for item in config.templates if item.name == category.template), None)
    if template is None:
        return None
    subject = (
        render_template_text(template.subject, email_input, contact)
        if template.subject
        else _re_subject(email_input.get("subject", "No Subject"))
    )
    return {
        "name": "write_email",
        "args": {
            "to": _email_address(email_input.get("author", "")),
            "subject": subject,
            "content": render_template_text(template.body, email_input, contact),
        },
        "id": f"category_{category.name}_template",
        "type": "tool_call",
    }
