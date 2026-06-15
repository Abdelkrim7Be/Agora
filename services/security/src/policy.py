from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from src.models import Decision

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_PATH = SERVICE_ROOT / "policy.yaml"


class RecipientPolicy(BaseModel):
    allow_domains: list[str] = Field(default_factory=list)
    deny_domains: list[str] = Field(default_factory=list)

    @field_validator("allow_domains", "deny_domains")
    @classmethod
    def _normalize_domains(cls, v: list[str]) -> list[str]:
        # Recipient domains are matched lowercased; normalize policy entries to match,
        # so an uppercase typo in policy.yaml can't silently bypass a deny rule.
        return [d.strip().lower() for d in v]


class LimitsPolicy(BaseModel):
    max_content_chars: int | None = None
    max_per_run: int | None = None
    max_per_day: int | None = None


class ToolPolicy(BaseModel):
    decision: Decision = "deny"
    recipients: RecipientPolicy | None = None
    limits: LimitsPolicy | None = None


class PolicyConfig(BaseModel):
    default: Decision = "deny"
    tools: dict[str, ToolPolicy] = Field(default_factory=dict)


def load_policy(path: str | Path | None = None) -> PolicyConfig:
    """Load and validate policy.yaml. Fails loud on missing file or empty content."""
    policy_path = Path(path) if path else DEFAULT_POLICY_PATH
    if not policy_path.is_file():
        raise FileNotFoundError(
            f"Policy config not found at {policy_path}. "
            "Copy policy.yaml into the service root and configure the capability rules."
        )
    data = yaml.safe_load(policy_path.read_text())
    if not data:
        raise ValueError(f"Policy config at {policy_path} is empty.")
    return PolicyConfig(**data)
