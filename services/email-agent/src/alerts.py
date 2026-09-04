from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
import yaml

from src.config import SERVICE_ROOT, settings
from src.cost_tracker import summarize as summarize_costs
from src.notifications import notify_admin_alert
from src.instance_config import read_instance_text, write_instance_text
from src.tenant import current_agent_instance_id

DEFAULT_ALERTS_PATH = SERVICE_ROOT / "alerts.yaml"
DEFAULT_ALERT_STATE_PATH = SERVICE_ROOT / "logs" / "alert_state.yaml"
logger = logging.getLogger(__name__)

COMPONENT_LABELS = {
    "agent": "Agent",
    "poller": "Poller",
    "security": "Securite",
    "database": "Base de donnees",
    "redis": "Redis",
}


class AlertSettings(BaseModel):
    enabled: bool = False
    admin_recipient: str = ""
    component_alerts: bool = True
    token_cap_enabled: bool = False
    daily_token_cap: int = Field(default=0, ge=0)
    token_cap_threshold: float = Field(default=0.8, ge=0.1, le=1.0)


def _settings_path() -> Path:
    return Path(settings.alerts_path or DEFAULT_ALERTS_PATH)


def _state_path() -> Path:
    return Path(settings.alerts_state_path or DEFAULT_ALERT_STATE_PATH)


def load_alert_settings(agent_instance_id: str | None = None) -> AlertSettings:
    """Instance settings on top of the deployment defaults.

    An instance that has never saved its own alert settings inherits whatever the
    stack was deployed with, so component and token-cap alerts do not silently
    stay off on a fresh install. An explicit saved value always wins.
    """
    raw = read_instance_text("alerts", _settings_path(), agent_instance_id=agent_instance_id)
    data = yaml.safe_load(raw) or {}
    defaults = {
        "enabled": settings.alerts_enabled_default,
        "admin_recipient": settings.alert_admin_recipient_default,
    }
    for key, value in defaults.items():
        if data.get(key) in (None, "", False) and value:
            data[key] = value
    return AlertSettings(**data)


def save_alert_settings(config: AlertSettings, agent_instance_id: str | None = None) -> AlertSettings:
    payload = yaml.safe_dump(config.model_dump(), sort_keys=False)
    write_instance_text("alerts", payload, _settings_path(), agent_instance_id=agent_instance_id)
    return config


def load_alert_state(agent_instance_id: str | None = None) -> dict[str, Any]:
    raw = read_instance_text("alert_state", _state_path(), agent_instance_id=agent_instance_id)
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        return {}
    return data


def save_alert_state(state: dict[str, Any], agent_instance_id: str | None = None) -> dict[str, Any]:
    payload = yaml.safe_dump(state, sort_keys=False)
    write_instance_text("alert_state", payload, _state_path(), agent_instance_id=agent_instance_id)
    return state


def _component_detail(component: str, snapshot: dict) -> str:
    if component == "poller" and snapshot.get("last_error"):
        return f" Derniere erreur: {snapshot['last_error']}."
    detail = snapshot.get("detail")
    if detail:
        return f" Detail: {detail}."
    return ""


def evaluate_alerts(health_snapshot: dict, now: datetime | None = None) -> list[dict[str, Any]]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    config = load_alert_settings()
    state = load_alert_state()
    events: list[dict[str, Any]] = []
    component_state = dict(state.get("components") or {})

    alerts_enabled = bool(config.enabled)

    def emit(kind: str, subject: str, body: str, **fields: Any) -> None:
        """Record the event, and mail it only when alerting is switched on.

        The event is recorded either way so a caller can see what would have
        been sent without turning delivery on.
        """
        sent = notify_admin_alert(config.admin_recipient, subject, body) if alerts_enabled else False
        events.append({"kind": kind, **fields, "sent": sent})

    if config.component_alerts:
        for key, label in COMPONENT_LABELS.items():
            snapshot = health_snapshot.get(key) or {}
            current_status = str(snapshot.get("status") or "unknown")
            previous_status = component_state.get(key)
            component_state[key] = current_status
            # Alerts fire on the transition, so a component that stays down is
            # reported once rather than on every evaluation.
            if previous_status == current_status:
                continue
            if current_status == "down":
                emit(
                    "component_down",
                    f"Alerte Agora : {label} hors service",
                    f"Le composant {label} de l'instance {current_agent_instance_id()} est passe a l'etat hors service."
                    f" Statut actuel: {current_status}."
                    f"{_component_detail(key, snapshot)}",
                    component=key,
                    status=current_status,
                )
            elif previous_status == "down" and current_status == "up":
                emit(
                    "component_up",
                    f"Resolution Agora : {label} de nouveau actif",
                    f"Le composant {label} de l'instance {current_agent_instance_id()} est revenu a l'etat actif.",
                    component=key,
                    status=current_status,
                )

    state["components"] = component_state

    if config.token_cap_enabled and config.daily_token_cap > 0:
        summary = summarize_costs(
            "day",
            user_id=None,
            agent_instance_id=current_agent_instance_id(),
        )
        total_tokens = int((summary.get("totals") or {}).get("total_tokens") or 0)
        threshold_tokens = max(1, int(config.daily_token_cap * config.token_cap_threshold))
        near_cap = total_tokens >= threshold_tokens
        previous_near_cap = state.get("token_cap_near")
        state["token_cap_near"] = near_cap
        state["token_cap_total_tokens"] = total_tokens
        state["token_cap_threshold_tokens"] = threshold_tokens
        # A first observation under the threshold is not a resolution — there
        # was never an alert to clear.
        crossed = near_cap if previous_near_cap is None else previous_near_cap != near_cap
        if crossed and near_cap:
            emit(
                "token_cap_near",
                "Alerte Agora : plafond de tokens bientot atteint",
                f"L'instance {current_agent_instance_id()} a consomme {total_tokens} tokens aujourd'hui, "
                f"au-dela du seuil d'alerte {threshold_tokens}/{config.daily_token_cap}.",
                total_tokens=total_tokens,
            )
        elif crossed:
            emit(
                "token_cap_clear",
                "Resolution Agora : consommation de tokens revenue sous le seuil",
                f"L'instance {current_agent_instance_id()} est revenue sous le seuil de tokens: "
                f"{total_tokens}/{config.daily_token_cap} aujourd'hui.",
                total_tokens=total_tokens,
            )

    state["updated_at"] = now.isoformat(timespec="seconds")
    save_alert_state(state)
    return events
