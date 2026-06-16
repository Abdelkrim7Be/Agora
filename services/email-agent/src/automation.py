from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from src.config import SERVICE_ROOT

DEFAULT_RULES_PATH = SERVICE_ROOT / "rules.yaml"

Classification = Literal["ignore", "notify", "respond"]


class RuleWhen(BaseModel):
    """Deterministic predicates for matching an email before the agent LLM runs."""

    sender_contains: list[str] = Field(default_factory=list)
    sender_domain: list[str] = Field(default_factory=list)
    subject_contains: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    classification: Classification | None = None


class RuleThen(BaseModel):
    """Actions a matched rule may request through the normal tool path."""

    labels: list[str] = Field(default_factory=list)
    archive: bool = False
    mark_read: bool = False
    notify: bool = False
    respond: str | None = None
    snooze_days: int | None = Field(default=None, ge=1)

    @field_validator("respond")
    @classmethod
    def non_blank_response(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("respond must not be blank")
        return value


class AutomationRule(BaseModel):
    name: str = Field(min_length=1)
    enabled: bool = True
    when: RuleWhen = Field(default_factory=RuleWhen)
    then: RuleThen


class DigestConfig(BaseModel):
    enabled: bool = False
    hour: int = Field(default=18, ge=0, le=23)
    statuses: list[str] = Field(default_factory=lambda: ["notify", "pending_approval"])


class SnoozeConfig(BaseModel):
    enabled: bool = False
    label_prefix: str = "Snoozed"
    max_resurface_per_run: int = Field(default=20, ge=1)


class FollowUpConfig(BaseModel):
    enabled: bool = False
    label: str = "Awaiting Reply"
    after_days: int = Field(default=3, ge=1)
    max_results: int = Field(default=10, ge=1)
    nudge: str = "Just following up on this."


class LearningConfig(BaseModel):
    enabled: bool = False
    suggestions_path: str = "rule_suggestions.jsonl"


class RulesConfig(BaseModel):
    enabled: bool = False
    rules: list[AutomationRule] = Field(default_factory=list)
    digest: DigestConfig = Field(default_factory=DigestConfig)
    snooze: SnoozeConfig = Field(default_factory=SnoozeConfig)
    follow_ups: FollowUpConfig = Field(default_factory=FollowUpConfig)
    learning: LearningConfig = Field(default_factory=LearningConfig)


def load_rules(path: str | Path | None = None) -> RulesConfig:
    """Load automation rules. Missing or empty files mean automation stays off."""
    rules_path = Path(path) if path else DEFAULT_RULES_PATH
    if not rules_path.is_file():
        return RulesConfig()
    data = yaml.safe_load(rules_path.read_text()) or {}
    return RulesConfig(**data)


def snooze_label(prefix: str, target: date) -> str:
    return f"{prefix.rstrip('/')}/{target.isoformat()}"
