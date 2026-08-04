"""Which mail provider an agent instance is connected to.

Stored per instance through `instance_config`, exactly like `send_mode`. The
email-agent is deliberately the single source of truth for this: it is the
component that holds the OAuth token, so it is the only one that can be wrong
in a way that matters. The gateway does not carry a provider column — it reads
the value back off `GET /sync/status`.
"""

from __future__ import annotations

from src.config import SERVICE_ROOT
from src.instance_config import read_instance_text, write_instance_text

MAIL_PROVIDERS = ("gmail", "outlook")
DEFAULT_PROVIDER = "gmail"

_KIND = "mail_provider"
# Default-instance storage path (non-default instances resolve to
# logs/instances/<id>/mail_provider.yaml through instance_config).
_DEFAULT_PATH = SERVICE_ROOT / "mail_provider.yaml"


def get_mail_provider(agent_instance_id: str | None = None) -> str:
    """The instance's provider; anything unset or unrecognised reads as Gmail.

    Defaulting to Gmail rather than erroring is what keeps every pre-existing
    instance working without a migration — none of them have this file.
    """
    raw = read_instance_text(_KIND, _DEFAULT_PATH, agent_instance_id).strip().lower()
    return raw if raw in MAIL_PROVIDERS else DEFAULT_PROVIDER


def set_mail_provider(provider: str, agent_instance_id: str | None = None) -> str:
    cleaned = (provider or "").strip().lower()
    if cleaned not in MAIL_PROVIDERS:
        raise ValueError(f"mail provider must be one of {MAIL_PROVIDERS}, got {provider!r}")
    write_instance_text(_KIND, cleaned, _DEFAULT_PATH, agent_instance_id)
    return cleaned
