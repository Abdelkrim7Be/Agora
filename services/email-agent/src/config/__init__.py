from __future__ import annotations

import json
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from src.managed_secrets import get_secret

load_dotenv()


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() == "true"


def _parse_user_map() -> dict[str, str]:
    """Map an external identity (e.g. a Gmail address) to the platform user id.

    Lets webhook-scoped runs share the same tenant key the gateway propagates via
    X-Agora-User. Empty/invalid -> {} (identity mapping, current behavior).
    """
    raw = os.getenv("AGENT_USER_MAP", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k).strip().lower(): str(v) for k, v in data.items()}
    except (ValueError, AttributeError):
        return {}


class Settings:
    openai_api_key: str = get_secret("AGENT", "OPENAI_API_KEY")
    anthropic_api_key: str = get_secret("AGENT", "ANTHROPIC_API_KEY")
    llm_profile: str = os.getenv("AGENT_LLM_PROFILE", "local")
    llm_config_path: str = os.getenv("AGENT_LLM_CONFIG_PATH", "")
    llm_streaming_enabled: bool = _env_bool("AGENT_LLM_STREAMING_ENABLED", "true")
    roles_path: str = os.getenv("AGENT_ROLES_PATH", "roles.yaml")
    contacts_path: str = os.getenv("AGENT_CONTACTS_PATH", "contacts.yaml")
    gmail_credentials_path: str = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    gmail_token_path: str = os.getenv("GMAIL_TOKEN_PATH", "token.json")
    gmail_token_store_path: str = os.getenv("GMAIL_TOKEN_STORE_PATH", "logs/gmail_tokens.json")
    gmail_oauth_redirect_uri: str = os.getenv("GMAIL_OAUTH_REDIRECT_URI", "http://localhost:8080/api/agent/connect/gmail/callback")
    gmail_oauth_state_secret: str = os.getenv("GMAIL_OAUTH_STATE_SECRET", "")
    token_encryption_key_file: str = os.getenv("AGENT_TOKEN_ENCRYPTION_KEY_FILE", "")
    token_encryption_key: str = os.getenv("AGENT_TOKEN_ENCRYPTION_KEY", "")
    token_encryption_required: bool = _env_bool("AGENT_TOKEN_ENCRYPTION_REQUIRED", "false")
    max_emails_per_run: int = int(os.getenv("AGENT_MAX_EMAILS_PER_RUN", "20"))
    poll_interval_minutes: float = float(os.getenv("AGENT_POLL_INTERVAL_MIN", "5"))
    poll_max_retries: int = int(os.getenv("AGENT_POLL_MAX_RETRIES", "3"))
    poll_backoff_base_seconds: float = float(os.getenv("AGENT_POLL_BACKOFF_BASE_SECONDS", "2"))
    # Cap how many of a thread's most-recent messages are fed as context (token budget).
    thread_max_messages: int = int(os.getenv("AGENT_THREAD_MAX_MESSAGES", "10"))
    dry_run: bool = _env_bool("AGENT_DRY_RUN", "true")
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))
    # Durable state files — shared by the API and the poller so a paused run started
    # by one is resumable by the other (same checkpoints/store on disk).
    checkpoints_db: str = os.getenv("AGENT_CHECKPOINTS_DB", "checkpoints.db")
    store_db: str = os.getenv("AGENT_STORE_DB", "store.db")
    # PDF text extraction (gated — default off to avoid downloading large files).
    extract_attachments: bool = _env_bool("AGENT_EXTRACT_ATTACHMENTS", "false")
    attachment_max_chars: int = int(os.getenv("AGENT_ATTACHMENT_MAX_CHARS", "3000"))
    # Security service integration (off by default — no behavior change until opted in).
    security_enabled: bool = _env_bool("AGENT_SECURITY_ENABLED", "false")
    security_url: str = os.getenv("AGENT_SECURITY_URL", "http://localhost:8001")
    security_timeout: float = float(os.getenv("AGENT_SECURITY_TIMEOUT", "10"))

    # Pending-approval email notifications (off by default). Routes through the same
    # connected Gmail mailbox as agent sends; recipient resolves via the role directory.
    notify_enabled: bool = _env_bool("AGENT_NOTIFY_ENABLED", "false")
    # Optional base URL of the web control panel, used to link back to the approval
    # instead of including the email body in the notification (Part H guardrail).
    notify_app_base_url: str = os.getenv("AGENT_NOTIFY_APP_BASE_URL", "")

    # Phase 4 platform mode. Empty DATABASE_URL keeps the current SQLite dev backend.
    database_url: str = get_secret("AGENT", "DATABASE_URL")
    redis_url: str = get_secret("AGENT", "REDIS_URL")
    storage_backend: str = os.getenv("AGENT_STORAGE_BACKEND", "postgres" if database_url else "sqlite")
    run_registry_backend: str = os.getenv("AGENT_RUN_REGISTRY_BACKEND", "postgres" if database_url else "json")
    cost_tracking_enabled: bool = _env_bool("AGENT_COST_TRACKING_ENABLED", "true")
    cost_backend: str = os.getenv("AGENT_COST_BACKEND", "postgres" if database_url else "json")
    costs_path: str = os.getenv("AGENT_COSTS_PATH", "logs/llm_costs.jsonl")
    trace_backend: str = os.getenv("AGENT_TRACE_BACKEND", "postgres" if database_url else "json")
    traces_path: str = os.getenv("AGENT_TRACES_PATH", "logs/llm_traces.jsonl")
    alerts_path: str = os.getenv("AGENT_ALERTS_PATH", "alerts.yaml")
    alerts_state_path: str = os.getenv("AGENT_ALERT_STATE_PATH", "logs/alert_state.yaml")
    retention_path: str = os.getenv("AGENT_RETENTION_PATH", "retention.yaml")
    dlq_backend: str = os.getenv("AGENT_DLQ_BACKEND", "redis" if redis_url else ("postgres" if database_url else "json"))
    dlq_path: str = os.getenv("AGENT_DLQ_PATH", "logs/dlq.json")
    tenant_mode: str = os.getenv("TENANT_MODE", "single")
    default_user_id: str = os.getenv("AGENT_DEFAULT_USER_ID", "default")
    default_agent_instance_id: str = os.getenv("AGENT_DEFAULT_INSTANCE_ID", "default-email-agent")
    gmail_webhook_enabled: bool = _env_bool("GMAIL_WEBHOOK_ENABLED", "false")
    gmail_webhook_topic: str = os.getenv("GMAIL_WEBHOOK_TOPIC", "")
    gmail_webhook_secret: str = os.getenv("GMAIL_WEBHOOK_SECRET", "")
    # Per-user last-processed Gmail historyId baseline for incremental push sync.
    gmail_sync_path: str = os.getenv("GMAIL_SYNC_PATH", "logs/gmail_sync.json")
    # Per-instance sync observability state (connection status, last success/failure, etc.).
    gmail_sync_status_path: str = os.getenv("GMAIL_SYNC_STATUS_PATH", "logs/gmail_sync_status.json")
    # Uploaded media (signature images, contact photos) — compose mounts /app/data/media.
    media_dir: str = os.getenv("AGENT_MEDIA_DIR", "logs/instances")
    # Soft per-instance hourly Gmail API call budget (health display + 80% alert).
    gmail_hourly_call_budget: int = int(os.getenv("GMAIL_HOURLY_CALL_BUDGET", "1000"))
    # Gmail watches expire after 7 days; re-register well inside that window.
    gmail_watch_renew_hours: int = int(os.getenv("GMAIL_WATCH_RENEW_HOURS", "24"))
    # Renew a watch once its recorded expiration is closer than this margin;
    # watches with no recorded expiration are renewed on every check.
    gmail_watch_renew_margin_hours: float = float(os.getenv("GMAIL_WATCH_RENEW_MARGIN_HOURS", "12"))
    polling_fallback_enabled: bool = _env_bool("GMAIL_POLLING_FALLBACK_ENABLED", "true")
    # Poll cadence when push webhooks are on and polling is only the safety net.
    webhook_fallback_poll_minutes: float = float(os.getenv("AGENT_WEBHOOK_FALLBACK_POLL_MIN", "10"))
    # External-identity -> platform user id (aligns webhook tenant key with the gateway).
    user_map: dict = _parse_user_map()


settings = Settings()


# --- Behavior config (config.yaml) — separate concern from Settings above. ---
# Settings = secrets & infra from .env (never committed).
# AgentConfig = behavior & persona from config.yaml (committed, customizable).

SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = SERVICE_ROOT / "config.yaml"


class AgentBehavior(BaseModel):
    """The customizable behavior of the email agent."""

    background: str = Field(min_length=1)
    triage_instructions: str = Field(min_length=1)
    response_preferences: str = Field(min_length=1)
    writing_style_default: str = "Neutral professional voice until learned from sent mail."


class StyleLearningConfig(BaseModel):
    """Opt-in style learning from the selected mailbox's sent mail."""

    enabled: bool = False
    max_samples: int = Field(default=8, ge=1, le=50)


class AutoOrganizeConfig(BaseModel):
    """Optional automatic inbox organization after triage."""

    enabled: bool = False
    ignored_label: str = "Auto/Ignored"


class AgentConfig(BaseModel):
    agent: AgentBehavior
    capabilities: dict[str, bool] = {"email": True}
    auto_organize: AutoOrganizeConfig = Field(default_factory=AutoOrganizeConfig)
    style_learning: StyleLearningConfig = Field(default_factory=StyleLearningConfig)


def load_config(path: str | Path | None = None) -> AgentConfig:
    """Load and validate config.yaml. Fails loud on missing file or empty/invalid fields."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if path is None:
        from src.instance_config import read_instance_text

        raw = read_instance_text("config", config_path)
    elif config_path.is_file():
        raw = config_path.read_text()
    else:
        raise FileNotFoundError(
            f"Agent config not found at {config_path}. "
            "Copy config.yaml into the service root and fill in the agent behavior."
        )
    data = yaml.safe_load(raw)
    if not data:
        raise ValueError(f"Agent config at {config_path} is empty.")
    return AgentConfig(**data)
