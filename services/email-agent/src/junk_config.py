"""Per-instance configuration for the deterministic junk gate.

The gate's built-in heuristics (Gmail category labels, bulk headers, sender
shape) stay in `src.junk_gate`; this module holds the parts a human should be
able to tune per mailbox: explicit sender/domain blocklists and allowlists, and
toggles for each heuristic family.

Stored through `src.instance_config` like the other per-instance YAML configs,
so a mailbox that never wants promotional mail drafted can say so without a
code change.
"""

from __future__ import annotations

from email.utils import parseaddr
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from src.config import SERVICE_ROOT

DEFAULT_JUNK_PATH = SERVICE_ROOT / "junk.yaml"
_KIND = "junk"


class JunkConfig(BaseModel):
    """Junk-gate settings. Defaults reproduce the built-in heuristic behavior."""

    enabled: bool = True
    # Explicit lists win over every heuristic; allow is checked before block.
    allowed_senders: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_senders: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    # Heuristic families, individually switchable.
    gmail_categories: bool = True
    bulk_headers: bool = True
    sender_heuristics: bool = True


def load_junk(agent_instance_id: str | None = None) -> JunkConfig:
    from src.instance_config import read_instance_text

    raw = read_instance_text(_KIND, DEFAULT_JUNK_PATH, agent_instance_id)
    data = yaml.safe_load(raw) if raw else None
    return JunkConfig(**(data or {}))


def dump_junk(config: JunkConfig) -> str:
    return yaml.safe_dump(config.model_dump(), sort_keys=False, allow_unicode=True)


def save_junk(config: JunkConfig, agent_instance_id: str | None = None) -> None:
    from src.instance_config import write_instance_text

    write_instance_text(_KIND, dump_junk(config), DEFAULT_JUNK_PATH, agent_instance_id)


def normalize_address(value: str) -> str:
    _name, address = parseaddr(value or "")
    return (address or value or "").strip().lower()


def address_domain(value: str) -> str:
    address = normalize_address(value)
    return address.rsplit("@", 1)[1] if "@" in address else ""


def domain_matches(domain: str, patterns: list[str]) -> bool:
    """True when `domain` equals a pattern or is a subdomain of one.

    `temu.com` in the list therefore also covers `mail.temu.com`.
    """
    if not domain:
        return False
    for pattern in patterns:
        cleaned = (pattern or "").strip().lower().lstrip("@").lstrip(".")
        if not cleaned:
            continue
        if domain == cleaned or domain.endswith(f".{cleaned}"):
            return True
    return False


def address_matches(address: str, patterns: list[str]) -> bool:
    if not address:
        return False
    return address in {normalize_address(p) for p in patterns if (p or "").strip()}
