from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() == "true"


class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    gmail_credentials_path: str = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    gmail_token_path: str = os.getenv("GMAIL_TOKEN_PATH", "token.json")
    gmail_token_store_path: str = os.getenv("GMAIL_TOKEN_STORE_PATH", "logs/gmail_tokens.json")
    token_encryption_key: str = os.getenv("AGENT_TOKEN_ENCRYPTION_KEY", "")
    default_llm_provider: str = os.getenv("DEFAULT_LLM_PROVIDER", "groq")
    max_emails_per_run: int = int(os.getenv("AGENT_MAX_EMAILS_PER_RUN", "20"))
    poll_interval_minutes: int = int(os.getenv("AGENT_POLL_INTERVAL_MIN", "5"))
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

    # Phase 4 platform mode. Empty DATABASE_URL keeps the current SQLite dev backend.
    database_url: str = os.getenv("DATABASE_URL", "")
    redis_url: str = os.getenv("REDIS_URL", "")
    storage_backend: str = os.getenv("AGENT_STORAGE_BACKEND", "postgres" if database_url else "sqlite")
    run_registry_backend: str = os.getenv("AGENT_RUN_REGISTRY_BACKEND", "postgres" if database_url else "json")
    tenant_mode: str = os.getenv("TENANT_MODE", "single")
    default_user_id: str = os.getenv("AGENT_DEFAULT_USER_ID", "default")
    gmail_webhook_enabled: bool = _env_bool("GMAIL_WEBHOOK_ENABLED", "false")
    polling_fallback_enabled: bool = _env_bool("GMAIL_POLLING_FALLBACK_ENABLED", "true")


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


class AutoOrganizeConfig(BaseModel):
    """Optional automatic inbox organization after triage."""

    enabled: bool = False
    ignored_label: str = "Auto/Ignored"


class AgentConfig(BaseModel):
    agent: AgentBehavior
    capabilities: dict[str, bool] = {"email": True}
    auto_organize: AutoOrganizeConfig = Field(default_factory=AutoOrganizeConfig)


def load_config(path: str | Path | None = None) -> AgentConfig:
    """Load and validate config.yaml. Fails loud on missing file or empty/invalid fields."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Agent config not found at {config_path}. "
            "Copy config.yaml into the service root and fill in the agent behavior."
        )
    data = yaml.safe_load(config_path.read_text())
    if not data:
        raise ValueError(f"Agent config at {config_path} is empty.")
    return AgentConfig(**data)
