from __future__ import annotations

import os

from dotenv import load_dotenv

from src.managed_secrets import get_secret

load_dotenv()


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() == "true"


class Settings:
    api_host: str = os.getenv("SECURITY_API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("SECURITY_API_PORT", "8001"))

    # Local-first: quarantine classifier runs on Ollama via the OpenAI-compatible
    # endpoint. Clear SECURITY_SANITIZE_ENDPOINT to use a hosted provider model.
    sanitize_model: str = os.getenv("SECURITY_SANITIZE_MODEL", "openai:qwen2.5:3b-8k")
    sanitize_endpoint: str = os.getenv("SECURITY_SANITIZE_ENDPOINT", "http://localhost:11434/v1")
    sanitize_always_llm: bool = os.getenv("SECURITY_SANITIZE_ALWAYS_LLM", "false").lower() == "true"
    sanitize_max_chars: int = int(os.getenv("SECURITY_SANITIZE_MAX_CHARS", "6000"))
    sanitize_timeout: float | None = (
        float(os.getenv("SECURITY_SANITIZE_TIMEOUT"))
        if os.getenv("SECURITY_SANITIZE_TIMEOUT")
        else None
    )
    # Same quarantine LLM as /sanitize, reused for the /audit-output escalation path.
    output_audit_always_llm: bool = os.getenv("SECURITY_OUTPUT_AUDIT_ALWAYS_LLM", "false").lower() == "true"

    policy_path: str = os.getenv("SECURITY_POLICY_PATH", "policy.yaml")

    database_url: str = get_secret("SECURITY", "DATABASE_URL")
    redis_url: str = get_secret("SECURITY", "REDIS_URL")
    ratelimit_backend: str = os.getenv("SECURITY_RATELIMIT_BACKEND", "redis" if redis_url else "memory")
    tenant_mode: str = os.getenv("TENANT_MODE", "single")


settings = Settings()
