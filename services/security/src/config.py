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
    # Fallback only — every deployment sets SECURITY_SANITIZE_MODEL explicitly to
    # whatever the agent runs, so the two share one resident model. Left on the
    # small local model because this default is what an unconfigured run (and the
    # test suite) reaches for.
    sanitize_model: str = os.getenv("SECURITY_SANITIZE_MODEL", "openai:qwen2.5:3b-8k")
    # Ceiling on the classifier's reply. Its output is a small structured verdict,
    # so a few hundred tokens is generous — but the call had no cap at all, and a
    # reasoning-style model answered a one-line classification with thousands of
    # tokens of deliberation, holding the single inference slot for minutes while
    # every other request in the platform queued behind it.
    sanitize_max_tokens: int = int(os.getenv("SECURITY_SANITIZE_MAX_TOKENS", "512"))
    sanitize_endpoint: str = os.getenv("SECURITY_SANITIZE_ENDPOINT", "http://localhost:11434/v1")
    sanitize_always_llm: bool = os.getenv("SECURITY_SANITIZE_ALWAYS_LLM", "false").lower() == "true"
    sanitize_max_chars: int = int(os.getenv("SECURITY_SANITIZE_MAX_CHARS", "6000"))
    # Default 60s rather than None. Unbounded, a classifier that stops producing
    # tokens holds the request — and the single inference slot behind it — for as
    # long as the model keeps going, which is how one message stalled the whole
    # platform. The sanitize path already degrades safely on error, so a timeout
    # is just another unavailable classifier.
    sanitize_timeout: float | None = float(os.getenv("SECURITY_SANITIZE_TIMEOUT", "60"))
    # Same quarantine LLM as /sanitize, reused for the /audit-output escalation path.
    output_audit_always_llm: bool = os.getenv("SECURITY_OUTPUT_AUDIT_ALWAYS_LLM", "false").lower() == "true"

    policy_path: str = os.getenv("SECURITY_POLICY_PATH", "policy.yaml")

    # Strip financial and personal identifiers from mail content before it can
    # reach a hosted model. Default on: the cost of redacting content that never
    # leaves the host is a few regex passes; the cost of not redacting content
    # that does is a personal-data incident.
    redact_pii: bool = os.getenv("SECURITY_REDACT_PII", "true").lower() == "true"

    # Shared with email-agent's AGENT_SECURITY_SHARED_SECRET — same value on both
    # sides. This service publishes no host port by default, so network isolation
    # is the first layer; this is the second, same tradeoff as the gateway's own
    # GATEWAY_AGENT_SHARED_SECRET (degrades open only if left unconfigured).
    shared_secret: str = os.getenv("AGENT_SECURITY_SHARED_SECRET", "")

    database_url: str = get_secret("SECURITY", "DATABASE_URL")
    redis_url: str = get_secret("SECURITY", "REDIS_URL")
    ratelimit_backend: str = os.getenv("SECURITY_RATELIMIT_BACKEND", "redis" if redis_url else "memory")
    tenant_mode: str = os.getenv("TENANT_MODE", "single")


settings = Settings()
