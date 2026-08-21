from __future__ import annotations

from typing import Any

from platform_core.db import TenantDatabase

from src.config import settings
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
)

_db = TenantDatabase(
    database_url=lambda: settings.database_url,
    tenant_mode=lambda: settings.tenant_mode,
    normalize_user_id=normalize_user_id,
    normalize_agent_instance_id=normalize_agent_instance_id,
    current_user_id=current_user_id,
    current_agent_instance_id=current_agent_instance_id,
)


def bind_tenant_context(
    connection: Any,
    *,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> Any:
    return _db.bind_tenant_context(
        connection, user_id=user_id, agent_instance_id=agent_instance_id
    )


def tenant_connection(**kwargs: Any):
    return _db.connect(**kwargs)


def validate_runtime_role() -> None:
    # Resolved at call time so the module-level name stays the patchable seam.
    return _db.validate_runtime_role(lambda: tenant_connection())
