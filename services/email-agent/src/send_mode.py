from __future__ import annotations

from src.config import SERVICE_ROOT, settings
from src.instance_config import read_instance_text, write_instance_text

SEND_MODES = ("simulation", "live")
SIMULATED_NOTE = "Simulé — aucun e-mail réel envoyé"

_KIND = "send_mode"
# Default-instance storage path (non-default instances resolve to
# logs/instances/<id>/send_mode.yaml through instance_config).
_DEFAULT_PATH = SERVICE_ROOT / "send_mode.yaml"


def get_send_mode(agent_instance_id: str | None = None) -> str:
    """The instance's send mode; absent or invalid config fails safe to simulation."""
    raw = read_instance_text(_KIND, _DEFAULT_PATH, agent_instance_id).strip().lower()
    return raw if raw in SEND_MODES else "simulation"


def set_send_mode(mode: str, agent_instance_id: str | None = None) -> str:
    cleaned = (mode or "").strip().lower()
    if cleaned not in SEND_MODES:
        raise ValueError(f"send_mode must be one of {SEND_MODES}, got {mode!r}")
    write_instance_text(_KIND, cleaned, _DEFAULT_PATH, agent_instance_id)
    return cleaned


def effective_dry_run(agent_instance_id: str | None = None) -> bool:
    """Whether outgoing actions must be simulated for the current instance.

    AGENT_DRY_RUN=true stays a global emergency lock that forces simulation
    everywhere; otherwise the per-instance send mode decides.
    """
    if settings.dry_run:
        return True
    return get_send_mode(agent_instance_id) != "live"
