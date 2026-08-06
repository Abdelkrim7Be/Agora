from __future__ import annotations

from typing import Any, Callable

# Settings are read through callables, not captured at construction, so a test
# that monkeypatches a service's settings object still reaches this code.


class TenantDatabase:
    """Postgres access that binds tenant identity to every session it opens.

    Row-level security policies read `agora.user_id` and
    `agora.agent_instance_id`, so a connection that skipped the binding would
    see nothing at best and another tenant's rows at worst.
    """

    def __init__(
        self,
        *,
        database_url: Callable[[], str],
        tenant_mode: Callable[[], str],
        normalize_user_id: Callable[[str | None], str],
        normalize_agent_instance_id: Callable[[str | None], str],
        current_user_id: Callable[[], str],
        current_agent_instance_id: Callable[[], str],
    ) -> None:
        self._database_url = database_url
        self._tenant_mode = tenant_mode
        self._normalize_user_id = normalize_user_id
        self._normalize_agent_instance_id = normalize_agent_instance_id
        self._current_user_id = current_user_id
        self._current_agent_instance_id = current_agent_instance_id

    def bind_tenant_context(
        self,
        connection: Any,
        *,
        user_id: str | None = None,
        agent_instance_id: str | None = None,
    ) -> Any:
        """Bind normalized tenant identity to a dedicated Postgres session."""
        resolved_user = self._normalize_user_id(user_id or self._current_user_id())
        resolved_instance = self._normalize_agent_instance_id(
            agent_instance_id or self._current_agent_instance_id()
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

    def connect(self, **kwargs: Any):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Tenant Postgres access requires psycopg.") from exc
        connection = psycopg.connect(self._database_url(), **kwargs)
        try:
            return self.bind_tenant_context(connection)
        except Exception:
            connection.close()
            raise

    def validate_runtime_role(self, connect: Callable[[], Any] | None = None) -> None:
        """Fail multi-tenant startup when the runtime role can bypass RLS.

        `connect` lets a caller supply its own factory rather than reach past
        this object to patch one.
        """
        if not self._database_url() or self._tenant_mode().lower().strip() != "multi":
            return
        with (connect or self.connect)() as connection:
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
