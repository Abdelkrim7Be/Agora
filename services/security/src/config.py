from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    api_host: str = os.getenv("SECURITY_API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("SECURITY_API_PORT", "8001"))

    sanitize_model: str = os.getenv("SECURITY_SANITIZE_MODEL", "groq:llama-3.3-70b-versatile")
    sanitize_always_llm: bool = os.getenv("SECURITY_SANITIZE_ALWAYS_LLM", "false").lower() == "true"
    sanitize_max_chars: int = int(os.getenv("SECURITY_SANITIZE_MAX_CHARS", "6000"))


settings = Settings()
