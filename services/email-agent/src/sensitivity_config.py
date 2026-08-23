"""Per-instance deterministic sensitivity gate configuration.

Unlike the junk gate, this defaults fully off. When configured, it lets an owner
name senders, domains, or subject keywords whose message bodies must not be
fetched into the agent pipeline.
"""

from __future__ import annotations


import yaml
from pydantic import BaseModel, Field

from src.config import SERVICE_ROOT
from src.junk_config import address_domain, address_matches, domain_matches, normalize_address

DEFAULT_SENSITIVITY_PATH = SERVICE_ROOT / "sensitivity.yaml"
_KIND = "sensitivity"


class SensitivityConfig(BaseModel):
    enabled: bool = False
    allowed_senders: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_senders: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    subject_keywords: list[str] = Field(default_factory=list)

    def has_rules(self) -> bool:
        return any(
            (
                self.blocked_senders,
                self.blocked_domains,
                self.subject_keywords,
            )
        )


def load_sensitivity(agent_instance_id: str | None = None) -> SensitivityConfig:
    from src.instance_config import read_instance_text

    raw = read_instance_text(_KIND, DEFAULT_SENSITIVITY_PATH, agent_instance_id)
    data = yaml.safe_load(raw) if raw else None
    return SensitivityConfig(**(data or {}))


def dump_sensitivity(config: SensitivityConfig) -> str:
    return yaml.safe_dump(config.model_dump(), sort_keys=False, allow_unicode=True)


def save_sensitivity(config: SensitivityConfig, agent_instance_id: str | None = None) -> None:
    from src.instance_config import write_instance_text

    write_instance_text(_KIND, dump_sensitivity(config), DEFAULT_SENSITIVITY_PATH, agent_instance_id)


__all__ = [
    "DEFAULT_SENSITIVITY_PATH",
    "SensitivityConfig",
    "address_domain",
    "address_matches",
    "domain_matches",
    "dump_sensitivity",
    "load_sensitivity",
    "normalize_address",
    "save_sensitivity",
]
