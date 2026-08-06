from __future__ import annotations

from platform_core.tenancy import TenantScope

from src.config import settings

_scope = TenantScope(
    default_user_id=lambda: settings.default_user_id,
    default_agent_instance_id=lambda: settings.default_agent_instance_id,
    user_map=lambda: settings.user_map,
)

normalize_user_id = _scope.normalize_user_id
normalize_agent_instance_id = _scope.normalize_agent_instance_id
resolve_user_id = _scope.resolve_user_id
current_user_id = _scope.current_user_id
current_agent_instance_id = _scope.current_agent_instance_id
user_context = _scope.user_context
agent_instance_context = _scope.agent_instance_context
