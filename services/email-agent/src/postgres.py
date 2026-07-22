from __future__ import annotations

from typing import Any

from src.config import settings
from src.tenant import (
    current_agent_instance_id,
    current_user_id,
    normalize_agent_instance_id,
    normalize_user_id,
)


def bind_tenant_context(
    connection: Any,
    *,
    user_id: str | None = None,
    agent_instance_id: str | None = None,
) -> Any:
    """Bind normalized tenant identity to a dedicated Postgres session."""
    resolved_user = normalize_user_id(user_id or current_user_id())
    resolved_instance = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                set_config('agora.user_id', %s, false),
                set_config('agora.agent_instance_id', %s, false)
            """,
            (resolved_user, resolved_instance),
        )
    return connection


def tenant_connection(**kwargs: Any):
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Tenant Postgres access requires psycopg.") from exc
    connection = psycopg.connect(settings.database_url, **kwargs)
    try:
        return bind_tenant_context(connection)
    except Exception:
        connection.close()
        raise


def validate_runtime_role() -> None:
    """Fail multi-tenant startup when the runtime role can bypass RLS."""
    if not settings.database_url or settings.tenant_mode.lower().strip() != "multi":
        return
    with tenant_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            row = cursor.fetchone()
    if not row or bool(row[0]) or bool(row[1]):
        raise RuntimeError(
            "Multi-tenant mode requires a PostgreSQL runtime role with "
            "NOSUPERUSER and NOBYPASSRLS"
        )
