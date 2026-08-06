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


LOCAL_LLM_PROFILES = frozenset({"local", "local-host", "local-docker", "safe"})


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
    # Send redacted content to the drafting model and restore the real values
    # just before the action runs. Defaults on for any non-local profile, since
    # that is exactly when mail content leaves the host.
    redact_for_model: bool = (
        os.getenv("AGENT_REDACT_FOR_MODEL", "").lower() == "true"
        or (
            os.getenv("AGENT_REDACT_FOR_MODEL", "") == ""
            and os.getenv("AGENT_LLM_PROFILE", "local") not in ("local", "local-host", "local-docker", "safe")
        )
    )
    llm_config_path: str = os.getenv("AGENT_LLM_CONFIG_PATH", "")
    llm_streaming_enabled: bool = _env_bool("AGENT_LLM_STREAMING_ENABLED", "true")
    llm_prompt_cache_enabled: bool = _env_bool("AGENT_LLM_PROMPT_CACHE_ENABLED", "true")
    roles_path: str = os.getenv("AGENT_ROLES_PATH", "roles.yaml")
    contacts_path: str = os.getenv("AGENT_CONTACTS_PATH", "contacts.yaml")
    gmail_credentials_path: str = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    gmail_token_path: str = os.getenv("GMAIL_TOKEN_PATH", "token.json")
    gmail_token_store_path: str = os.getenv("GMAIL_TOKEN_STORE_PATH", "logs/gmail_tokens.json")
    gmail_oauth_redirect_uri: str = os.getenv("GMAIL_OAUTH_REDIRECT_URI", "http://localhost:8080/api/agent/connect/gmail/callback")
    # Where to send the browser after the Gmail OAuth callback finishes. Empty
    # means "same origin, relative redirect" — correct in prod, where Traefik
    # puts the web app and gateway behind one public origin. Local dev splits
    # them across ports, so the override compose sets this explicitly.
    app_base_url: str = os.getenv("AGENT_APP_BASE_URL", "").rstrip("/")
    gmail_oauth_state_secret: str = os.getenv("GMAIL_OAUTH_STATE_SECRET", "")
    # Microsoft Graph. Unset by default — an instance only reaches this path
    # once its provider setting says "outlook", so Gmail-only deployments never
    # need an Azure app registration. "common" accepts both work/school and
    # personal accounts; pin it to a directory id for single-tenant.
    outlook_client_id: str = os.getenv("OUTLOOK_CLIENT_ID", "")
    outlook_client_secret: str = os.getenv("OUTLOOK_CLIENT_SECRET", "")
    outlook_tenant: str = os.getenv("OUTLOOK_TENANT", "common").strip() or "common"
    outlook_oauth_redirect_uri: str = os.getenv(
        "OUTLOOK_OAUTH_REDIRECT_URI",
        "http://localhost:8080/api/agent/connect/outlook/callback",
    )
    token_encryption_key_file: str = os.getenv("AGENT_TOKEN_ENCRYPTION_KEY_FILE", "")
    token_encryption_key: str = os.getenv("AGENT_TOKEN_ENCRYPTION_KEY", "")
    token_encryption_required: bool = _env_bool("AGENT_TOKEN_ENCRYPTION_REQUIRED", "false")
    token_store_backend: str = os.getenv("AGENT_TOKEN_STORE_BACKEND", "file").strip().lower()
    token_work_dir: str = os.getenv("AGENT_TOKEN_WORK_DIR", "/tmp/agora-token-work")
    token_vault_path: str = os.getenv(
        "AGENT_TOKEN_VAULT_PATH", "secret/data/agora/gmail-tokens"
    ).strip()
    max_emails_per_run: int = int(os.getenv("AGENT_MAX_EMAILS_PER_RUN", "20"))
    poll_interval_minutes: float = float(os.getenv("AGENT_POLL_INTERVAL_MIN", "5"))
    poll_max_retries: int = int(os.getenv("AGENT_POLL_MAX_RETRIES", "3"))
    poll_backoff_base_seconds: float = float(os.getenv("AGENT_POLL_BACKOFF_BASE_SECONDS", "2"))
    # /events SSE: how often the stream re-checks the run registry for changes.
    # The API and poller are separate processes sharing the same store, so this is
    # a server-side change-detection loop, not a client-visible poll.
    events_poll_interval_seconds: float = float(os.getenv("AGENT_EVENTS_POLL_INTERVAL_SECONDS", "2"))
    triage_cache_ttl_seconds: int = int(os.getenv("AGENT_TRIAGE_CACHE_TTL_SECONDS", "604800"))
    # Cap how many of a thread's most-recent messages are fed as context (token budget).
    thread_max_messages: int = int(os.getenv("AGENT_THREAD_MAX_MESSAGES", "10"))
    dry_run: bool = _env_bool("AGENT_DRY_RUN", "true")
    default_send_mode: str = os.getenv("AGENT_DEFAULT_SEND_MODE", "simulation").strip().lower()
    # Hard cap on who the agent may ever send real mail to, enforced at the Gmail
    # send helpers themselves — below the security service, below dry-run, below
    # any policy or model decision. Empty (the default) means no restriction;
    # set it when running live tests so an unattended agent cannot reach anyone
    # outside a known set of mailboxes.
    outbound_allowlist: frozenset[str] = frozenset(
        entry.strip().lower()
        for entry in os.getenv("AGENT_OUTBOUND_ALLOWLIST", "").split(",")
        if entry.strip()
    )
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
    # 10s was tuned for a hosted classifier call; against a single shared local
    # Ollama instance that's also serving triage/draft/persona generation, the
    # classifier queues behind whatever else is running and 10s isn't enough —
    # every email hit classifier_unavailable, got parked at security_hold, and
    # was reprocessed as a brand-new run every poll cycle, forever, without
    # ever actually completing.
    security_timeout: float = float(os.getenv("AGENT_SECURITY_TIMEOUT", "180"))
    # Put a drafted reply through the full quarantined classifier before it
    # becomes approvable. /sanitize skips that classifier when no heuristic
    # keyword fires, so without this a carefully worded injection is never
    # actually classified. Scoped to messages that produced a draft, which keeps
    # the slow local model off the rest of the mailbox.
    security_deep_check_drafts: bool = _env_bool("AGENT_SECURITY_DEEP_CHECK_DRAFTS", "true")
    # Domains treated as "internal" for a category's external_send_allowed=false
    # guard (comma-separated, case-insensitive). Independent of security_enabled —
    # this is a local workflow-policy rule, not the external security service.
    internal_domains: tuple[str, ...] = tuple(
        d.strip().lower() for d in os.getenv("AGENT_INTERNAL_DOMAINS", "").split(",") if d.strip()
    )

    # Pending-approval email notifications (off by default). Routes through the same
    # connected Gmail mailbox as agent sends; recipient resolves via the role directory.
    notify_enabled: bool = _env_bool("AGENT_NOTIFY_ENABLED", "false")
    # Optional base URL of the web control panel, used to link back to the approval
    # instead of including the email body in the notification (Part H guardrail).
    notify_app_base_url: str = os.getenv("AGENT_NOTIFY_APP_BASE_URL", "")

    # Phase 4 platform mode. Empty DATABASE_URL keeps the current SQLite dev backend.
    database_url: str = get_secret("AGENT", "DATABASE_URL")
    migration_database_url: str = get_secret("AGENT", "MIGRATION_DATABASE_URL") or database_url
    run_migrations: bool = _env_bool("AGENT_RUN_MIGRATIONS", "true")
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
    # Deployment-wide defaults for a fresh instance that has never saved its own
    # alert settings. Without these, component and token-cap alerts stayed off on
    # every new deployment until someone opened the settings page.
    alerts_enabled_default: bool = _env_bool("AGENT_ALERTS_ENABLED", "false")
    alert_admin_recipient_default: str = os.getenv("AGENT_ALERT_ADMIN_RECIPIENT", "")
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
    # local (default, dev/single-VPS) | s3 (OVH Object Storage / any S3-compatible bucket in prod).
    media_backend: str = os.getenv("AGENT_MEDIA_BACKEND", "local")
    media_s3_bucket: str = os.getenv("AGENT_MEDIA_S3_BUCKET", "")
    media_s3_endpoint_url: str = os.getenv("AGENT_MEDIA_S3_ENDPOINT_URL", "")
    media_s3_region: str = os.getenv("AGENT_MEDIA_S3_REGION", "")
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

    # S-scale-2: poller becomes a producer (enqueues) and one or more `src.worker`
    # processes claim + process jobs via Postgres SKIP LOCKED. Off by default —
    # the poller keeps processing inline, single-process, like before.
    job_queue_enabled: bool = _env_bool("AGENT_JOB_QUEUE_ENABLED", "false")
    # A worker crash between claim and done leaves a job stuck 'processing'; a
    # stale claim older than this is requeued.
    job_queue_stale_seconds: float = float(os.getenv("AGENT_JOB_QUEUE_STALE_SECONDS", "300"))
    job_queue_max_attempts: int = int(os.getenv("AGENT_JOB_QUEUE_MAX_ATTEMPTS", "5"))
    job_queue_poll_seconds: float = float(os.getenv("AGENT_JOB_QUEUE_POLL_SECONDS", "2"))

    # Instance onboarding pipeline (Phase 6 delta — see instance_setup.py).
    setup_enabled: bool = _env_bool("AGENT_SETUP_PIPELINE_ENABLED", "true")
    # Onboarding reads the mailbox once and everything downstream — contacts,
    # categories, style, persona, the first drafts — is built from that single
    # sample. 50 was too thin a slice to characterise a real mailbox, so the
    # workspace opened on a directory and a style profile drawn from a fortnight
    # of mail. Fetching headers for 200 is a handful of batched calls; only the
    # backlog triage below spends model time per message.
    setup_recent_limit: int = int(os.getenv("AGENT_SETUP_RECENT_LIMIT", "200"))
    setup_backlog_limit: int = int(os.getenv("AGENT_SETUP_BACKLOG_LIMIT", "20"))
    setup_sent_sample: int = int(os.getenv("AGENT_SETUP_SENT_SAMPLE", "50"))
    setup_llm_step_timeout_seconds: float = float(os.getenv("AGENT_SETUP_LLM_STEP_TIMEOUT_SECONDS", "120"))
    setup_backlog_message_timeout_seconds: float = float(os.getenv("AGENT_SETUP_BACKLOG_MESSAGE_TIMEOUT_SECONDS", "90"))
    setup_stale_seconds: float = float(os.getenv("AGENT_SETUP_STALE_SECONDS", "300"))
    setup_max_attempts: int = int(os.getenv("AGENT_SETUP_MAX_ATTEMPTS", "3"))
    instance_setup_path: str = os.getenv("AGENT_INSTANCE_SETUP_PATH", "logs/instance_setup.json")

    # In-app notification centre (Phase 6 delta — see notification_store.py).
    notification_store_path: str = os.getenv("AGENT_NOTIFICATION_STORE_PATH", "logs/notifications.json")
    notification_retention_days: int = int(os.getenv("AGENT_NOTIFICATION_RETENTION_DAYS", "90"))


settings = Settings()


def active_llm_profile_name(config: Settings | None = None) -> str:
    config = config or settings
    return (os.getenv("AGENT_LLM_PROFILE", config.llm_profile).strip() or "local").lower()


def validate_model_redaction(config: Settings | None = None) -> None:
    config = config or settings
    profile = active_llm_profile_name(config)
    if profile in LOCAL_LLM_PROFILES or config.redact_for_model:
        return
    raise RuntimeError(
        "AGENT_REDACT_FOR_MODEL=false is not allowed with hosted LLM profile "
        f"'{profile}'. Use a local profile or enable model redaction."
    )


def validate_gmail_webhook_config(config: Settings | None = None) -> None:
    config = config or settings
    if not config.gmail_webhook_enabled:
        return
    missing = []
    if not config.gmail_webhook_topic.strip():
        missing.append("GMAIL_WEBHOOK_TOPIC")
    if not config.gmail_webhook_secret.strip():
        missing.append("GMAIL_WEBHOOK_SECRET")
    if missing:
        raise RuntimeError(
            "Gmail webhooks are enabled but required settings are missing: "
            + ", ".join(missing)
        )
    if not config.polling_fallback_enabled:
        raise RuntimeError(
            "GMAIL_POLLING_FALLBACK_ENABLED must stay true while Gmail webhooks are enabled."
        )


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
