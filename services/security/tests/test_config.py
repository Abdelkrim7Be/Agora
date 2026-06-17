from __future__ import annotations

from src.config import settings


def test_platform_settings_default_to_memory_backend():
    assert settings.database_url == ""
    assert settings.redis_url == ""
    assert settings.ratelimit_backend == "memory"
    assert settings.tenant_mode == "single"
