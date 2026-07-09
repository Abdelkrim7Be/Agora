from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from src.config import SERVICE_ROOT, settings
from src.tenant import current_agent_instance_id, normalize_agent_instance_id

_roles_path = Path(settings.roles_path)
DEFAULT_ROLES_PATH = _roles_path if _roles_path.is_absolute() else SERVICE_ROOT / _roles_path


class RoleDirectoryError(RuntimeError):
    """Base error for role-directory operations."""


class RoleConflictError(RoleDirectoryError):
    """Raised when creating a duplicate role key."""


class RoleNotFoundError(RoleDirectoryError):
    """Raised when a requested role key does not exist."""


class RoleTarget(BaseModel):
    display_name: str = Field(min_length=1)
    emails: list[str] = Field(default_factory=list)
    dept: str | None = None

    @field_validator("display_name")
    @classmethod
    def _clean_display_name(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("display_name must not be blank")
        return cleaned

    @field_validator("dept")
    @classmethod
    def _clean_dept(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("emails")
    @classmethod
    def _clean_emails(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            email = str(value).strip().lower()
            if not email:
                continue
            if "@" not in email:
                raise ValueError(f"invalid email target: {value}")
            if email in seen:
                continue
            seen.add(email)
            cleaned.append(email)
        if not cleaned:
            raise ValueError("at least one email target is required")
        return cleaned

    @property
    def primary_email(self) -> str | None:
        return self.emails[0] if self.emails else None


class ResolvedRole(RoleTarget):
    role_key: str = Field(min_length=1)


class RolesConfig(BaseModel):
    roles: dict[str, RoleTarget] = Field(default_factory=dict)


def normalize_role_key(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalized_config(config: RolesConfig) -> RolesConfig:
    normalized: dict[str, RoleTarget] = {}
    for key, role in config.roles.items():
        normalized_key = normalize_role_key(key)
        if not normalized_key:
            continue
        normalized[normalized_key] = role if isinstance(role, RoleTarget) else RoleTarget(**role)
    return RolesConfig(roles=dict(sorted(normalized.items())))


def _config_from_data(data: dict | None) -> RolesConfig:
    payload = dict(data or {})
    payload.setdefault("roles", {})
    return _normalized_config(RolesConfig(**payload))


def _load_yaml_roles(path: Path) -> RolesConfig:
    raw = path.read_text() if path.is_file() else ""
    return _config_from_data(yaml.safe_load(raw) or {})


def dump_roles(config: RolesConfig) -> str:
    normalized = _normalized_config(config)
    payload = {
        "roles": {
            key: role.model_dump(exclude_none=True)
            for key, role in normalized.roles.items()
        }
    }
    return yaml.safe_dump(payload, sort_keys=False)


def _connect():
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - exercised only with Postgres installed
        raise RuntimeError("Postgres role directory requires psycopg.") from exc
    return psycopg.connect(settings.database_url)


def _default_instance_id() -> str:
    return normalize_agent_instance_id(settings.default_agent_instance_id)


def _local_path(default_path: Path, instance_id: str) -> Path:
    if instance_id == _default_instance_id():
        return default_path
    return SERVICE_ROOT / "logs" / "instances" / instance_id / default_path.name


def _select_rows(cur, agent_instance_id: str) -> list[tuple[str, str, str | None, list[str] | str]]:
    cur.execute(
        "SELECT role_key, display_name, dept, emails "
        "FROM email_agent_roles WHERE agent_instance_id = %s ORDER BY role_key",
        (agent_instance_id,),
    )
    return list(cur.fetchall())


def _config_from_rows(rows: list[tuple[str, str, str | None, list[str] | str]]) -> RolesConfig:
    payload: dict[str, RoleTarget] = {}
    for role_key, display_name, dept, emails in rows:
        if isinstance(emails, str):
            emails = json.loads(emails)
        payload[normalize_role_key(role_key)] = RoleTarget(
            display_name=display_name,
            dept=dept,
            emails=list(emails or []),
        )
    return _normalized_config(RolesConfig(roles=payload))


def load_roles(path: str | Path | None = None, agent_instance_id: str | None = None) -> RolesConfig:
    if path is not None:
        return _load_yaml_roles(Path(path))

    instance_id = normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())
    if settings.database_url:
        with _connect() as conn:
            with conn.cursor() as cur:
                rows = _select_rows(cur, instance_id)
                if rows:
                    return _config_from_rows(rows)
                if instance_id != _default_instance_id():
                    default_rows = _select_rows(cur, _default_instance_id())
                    if default_rows:
                        return _config_from_rows(default_rows)
        return _load_yaml_roles(DEFAULT_ROLES_PATH)

    path = _local_path(DEFAULT_ROLES_PATH, instance_id)
    if path.is_file():
        return _load_yaml_roles(path)
    return _load_yaml_roles(DEFAULT_ROLES_PATH)


def save_roles(
    config: RolesConfig,
    path: str | Path | None = None,
    agent_instance_id: str | None = None,
) -> None:
    normalized = _normalized_config(config)
    if path is not None:
        Path(path).write_text(dump_roles(normalized))
        return

    instance_id = normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())
    if settings.database_url:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM email_agent_roles WHERE agent_instance_id = %s", (instance_id,))
                for role_key, role in normalized.roles.items():
                    cur.execute(
                        """
                        INSERT INTO email_agent_roles (
                            agent_instance_id, role_key, display_name, dept, emails, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
                        """,
                        (
                            instance_id,
                            role_key,
                            role.display_name,
                            role.dept,
                            json.dumps(role.emails),
                        ),
                    )
        return

    file_path = _local_path(DEFAULT_ROLES_PATH, instance_id)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_name(file_path.name + ".tmp")
    temporary.write_text(dump_roles(normalized))
    temporary.replace(file_path)


def list_roles(agent_instance_id: str | None = None) -> list[ResolvedRole]:
    config = load_roles(agent_instance_id=agent_instance_id)
    return [
        ResolvedRole(role_key=role_key, **role.model_dump())
        for role_key, role in sorted(config.roles.items())
    ]


def resolve_role(role_key: str | None, agent_instance_id: str | None = None) -> ResolvedRole | None:
    normalized = normalize_role_key(role_key)
    if not normalized:
        return None
    config = load_roles(agent_instance_id=agent_instance_id)
    role = config.roles.get(normalized)
    if role is None:
        return None
    return ResolvedRole(role_key=normalized, **role.model_dump())


def create_role(
    role_key: str,
    display_name: str,
    emails: list[str],
    dept: str | None = None,
    agent_instance_id: str | None = None,
) -> ResolvedRole:
    normalized = normalize_role_key(role_key)
    if not normalized:
        raise ValueError("role_key must not be blank")
    config = load_roles(agent_instance_id=agent_instance_id)
    if normalized in config.roles:
        raise RoleConflictError(f"role '{normalized}' already exists")
    config.roles[normalized] = RoleTarget(display_name=display_name, dept=dept, emails=emails)
    save_roles(config, agent_instance_id=agent_instance_id)
    return ResolvedRole(role_key=normalized, **config.roles[normalized].model_dump())


def update_role(
    role_key: str,
    display_name: str,
    emails: list[str],
    dept: str | None = None,
    agent_instance_id: str | None = None,
) -> ResolvedRole:
    normalized = normalize_role_key(role_key)
    config = load_roles(agent_instance_id=agent_instance_id)
    if normalized not in config.roles:
        raise RoleNotFoundError(f"role '{normalized}' not found")
    config.roles[normalized] = RoleTarget(display_name=display_name, dept=dept, emails=emails)
    save_roles(config, agent_instance_id=agent_instance_id)
    return ResolvedRole(role_key=normalized, **config.roles[normalized].model_dump())


def delete_role(role_key: str, agent_instance_id: str | None = None) -> None:
    normalized = normalize_role_key(role_key)
    config = load_roles(agent_instance_id=agent_instance_id)
    if normalized not in config.roles:
        raise RoleNotFoundError(f"role '{normalized}' not found")
    del config.roles[normalized]
    save_roles(config, agent_instance_id=agent_instance_id)
