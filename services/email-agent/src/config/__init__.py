from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    gmail_credentials_path: str = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    gmail_token_path: str = os.getenv("GMAIL_TOKEN_PATH", "token.json")
    default_llm_provider: str = os.getenv("DEFAULT_LLM_PROVIDER", "openai")
    max_emails_per_run: int = int(os.getenv("AGENT_MAX_EMAILS_PER_RUN", "20"))
    dry_run: bool = os.getenv("AGENT_DRY_RUN", "true").lower() == "true"
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))


settings = Settings()
