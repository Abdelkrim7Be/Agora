from __future__ import annotations

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


class Category(BaseModel):
    name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    priority: Literal["urgent", "normal", "low"] = "normal"
    when: RuleWhen = Field(default_factory=RuleWhen)
    template: str | None = None
    policy: Literal["auto_draft", "notify", "organize", "ignore"] = "notify"
    labels: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("category name must not be blank")
        return cleaned


class Contact(BaseModel):
    email: str = Field(min_length=1)
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
    if not categories_path.is_file():
        return CategoriesConfig()
    data = yaml.safe_load(categories_path.read_text()) or {}
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
    sender = email_input.get("author", "")
    subject = email_input.get("subject", "")
    labels = set(email_input.get("labels", []))
    if when.sender_contains and not _contains_any(sender, when.sender_contains):
        return False
    if when.sender_domain and _sender_domain(sender) not in {d.lower() for d in when.sender_domain}:
        return False
    if when.subject_contains and not _contains_any(subject, when.subject_contains):
        return False
    if when.labels and not set(when.labels).issubset(labels):
        return False
    return True


def classify_category(email_input: dict, config: CategoriesConfig) -> dict:
    if not config.enabled:
        return {"category": None, "priority": "normal", "template": None, "policy": None}

    sender_address = _email_address(email_input.get("author", ""))
    category_by_name = {category.name: category for category in config.categories}
    for contact in config.contacts:
        if _email_address(contact.email) == sender_address and contact.category in category_by_name:
            category = category_by_name[contact.category]
            return {
                "category": category.name,
                "category_display_name": category.display_name,
                "priority": contact.priority or category.priority,
                "template": category.template,
                "policy": category.policy,
            }

    for category in config.categories:
        if matches_when(category.when, email_input):
            return {
                "category": category.name,
                "category_display_name": category.display_name,
                "priority": category.priority,
                "template": category.template,
                "policy": category.policy,
            }
    return {"category": None, "priority": "normal", "template": None, "policy": None}
