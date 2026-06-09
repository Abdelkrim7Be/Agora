from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    gmail_credentials_path: str = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    gmail_token_path: str = os.getenv("GMAIL_TOKEN_PATH", "token.json")
    default_llm_provider: str = os.getenv("DEFAULT_LLM_PROVIDER", "groq")
    max_emails_per_run: int = int(os.getenv("AGENT_MAX_EMAILS_PER_RUN", "20"))
    dry_run: bool = os.getenv("AGENT_DRY_RUN", "true").lower() == "true"
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))


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


class AgentConfig(BaseModel):
    agent: AgentBehavior
    capabilities: dict[str, bool] = {"email": True}


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
