from __future__ import annotations

from pathlib import Path
from threading import Lock

from src.config import SERVICE_ROOT, settings
from src.tenant import current_agent_instance_id, normalize_agent_instance_id

_schema_ready = False
_schema_lock = Lock()


def _connect():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Postgres instance configuration requires psycopg.") from exc
    return psycopg.connect(settings.database_url)


def setup_instance_config() -> None:
    global _schema_ready
    if not settings.database_url or _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        # Postgres schema is owned by Alembic migrations. Local file mode stays unchanged.
        _schema_ready = True


def _local_path(kind: str, default_path: Path, instance_id: str) -> Path:
    default_instance = normalize_agent_instance_id(settings.default_agent_instance_id)
    if instance_id == default_instance:
        return default_path
    return SERVICE_ROOT / "logs" / "instances" / instance_id / f"{kind}.yaml"


def read_instance_text(
    kind: str,
    default_path: str | Path,
    agent_instance_id: str | None = None,
) -> str:
    default = Path(default_path)
    instance_id = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    if settings.database_url:
        setup_instance_config()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content FROM email_agent_instance_config "
                    "WHERE agent_instance_id = %s AND config_kind = %s",
                    (instance_id, kind),
                )
                row = cur.fetchone()
                if row:
                    return row[0]
        return default.read_text() if default.is_file() else ""

    path = _local_path(kind, default, instance_id)
    if path.is_file():
        return path.read_text()
    return default.read_text() if default.is_file() else ""


def write_instance_text(
    kind: str,
    content: str,
    default_path: str | Path,
    agent_instance_id: str | None = None,
) -> None:
    default = Path(default_path)
    instance_id = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    if settings.database_url:
        setup_instance_config()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO email_agent_instance_config (
                        agent_instance_id, config_kind, content, revision, updated_at
                    )
                    VALUES (%s, %s, %s, 1, NOW())
                    ON CONFLICT (agent_instance_id, config_kind) DO UPDATE SET
                        content = EXCLUDED.content,
                        revision = email_agent_instance_config.revision + 1,
                        updated_at = NOW()
                    """,
                    (instance_id, kind, content),
                )
        return

    path = _local_path(kind, default, instance_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content)
    temporary.replace(path)
