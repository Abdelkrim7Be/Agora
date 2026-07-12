from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from src.config import SERVICE_ROOT, settings
from src.tenant import current_agent_instance_id, normalize_agent_instance_id

AUDIENCE_VALUES = ("employee", "client", "supplier", "prospect", "candidate", "partner")
Audience = Literal["employee", "client", "supplier", "prospect", "candidate", "partner"]

_contacts_path = Path(settings.contacts_path)
DEFAULT_CONTACTS_PATH = _contacts_path if _contacts_path.is_absolute() else SERVICE_ROOT / _contacts_path


class ContactDirectoryError(RuntimeError):
    """Base error for contact-directory operations."""


class ContactConflictError(ContactDirectoryError):
    """Raised when creating a duplicate contact."""


class ContactNotFoundError(ContactDirectoryError):
    """Raised when a requested contact does not exist."""


class SegmentConflictError(ContactDirectoryError):
    """Raised when creating a duplicate segment."""


class SegmentNotFoundError(ContactDirectoryError):
    """Raised when a requested segment does not exist."""


class Contact(BaseModel):
    email: str = Field(min_length=3)
    name: str | None = None
    audience: Audience
    fields: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    active: bool = True

    @field_validator("email")
    @classmethod
    def _clean_email(cls, value: str) -> str:
        cleaned = str(value).strip().lower()
        if "@" not in cleaned:
            raise ValueError("contact email must contain @")
        return cleaned

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("audience")
    @classmethod
    def _clean_audience(cls, value: str) -> str:
        cleaned = str(value).strip().lower()
        if cleaned not in AUDIENCE_VALUES:
            raise ValueError(f"audience must be one of: {', '.join(AUDIENCE_VALUES)}")
        return cleaned

    @field_validator("fields")
    @classmethod
    def _clean_fields(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, item in (value or {}).items():
            field_key = str(key).strip()
            field_value = str(item).strip()
            if field_key and field_value:
                cleaned[field_key] = field_value
        return cleaned

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()
        for item in value or []:
            cleaned = str(item).strip().lower()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            tags.append(cleaned)
        return tags


class Segment(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    match: dict[str, str] = Field(default_factory=dict)
    members: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _clean_id(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("segment id must not be blank")
        return cleaned

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("segment name must not be blank")
        return cleaned

    @field_validator("match")
    @classmethod
    def _clean_match(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, item in (value or {}).items():
            match_key = str(key).strip()
            match_value = str(item).strip()
            if match_key and match_value:
                cleaned[match_key] = match_value
        return cleaned

    @field_validator("members")
    @classmethod
    def _clean_members(cls, value: list[str]) -> list[str]:
        members: list[str] = []
        seen: set[str] = set()
        for item in value or []:
            cleaned = str(item).strip().lower()
            if not cleaned:
                continue
            if "@" not in cleaned:
                raise ValueError(f"segment member email must contain @: {item}")
            if cleaned in seen:
                continue
            seen.add(cleaned)
            members.append(cleaned)
        return members


class ContactsConfig(BaseModel):
    contacts: list[Contact] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)


def _connect():
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Postgres contact directory requires psycopg.") from exc
    return psycopg.connect(settings.database_url)


def _default_instance_id() -> str:
    return normalize_agent_instance_id(settings.default_agent_instance_id)


def _local_path(default_path: Path, instance_id: str) -> Path:
    if instance_id == _default_instance_id():
        return default_path
    return SERVICE_ROOT / "logs" / "instances" / instance_id / default_path.name


def _normalize_config(config: ContactsConfig) -> ContactsConfig:
    contacts_by_email = {contact.email: contact for contact in config.contacts}
    segments_by_id = {segment.id: segment for segment in config.segments}
    return ContactsConfig(
        contacts=[contacts_by_email[email] for email in sorted(contacts_by_email)],
        segments=[segments_by_id[segment_id] for segment_id in sorted(segments_by_id)],
    )


def _config_from_data(data: dict | None) -> ContactsConfig:
    payload = dict(data or {})
    payload.setdefault("contacts", [])
    payload.setdefault("segments", [])
    return _normalize_config(ContactsConfig(**payload))


def load_contacts(path: str | Path | None = None, agent_instance_id: str | None = None) -> ContactsConfig:
    if path is not None:
        file_path = Path(path)
        raw = file_path.read_text() if file_path.is_file() else ""
        return _config_from_data(yaml.safe_load(raw) or {})

    instance_id = normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())
    if settings.database_url:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT email, name, audience, fields, tags, active "
                    "FROM email_agent_contacts WHERE agent_instance_id = %s ORDER BY email",
                    (instance_id,),
                )
                contacts_rows = list(cur.fetchall())
                cur.execute(
                    "SELECT segment_id, name, match, members "
                    "FROM email_agent_segments WHERE agent_instance_id = %s ORDER BY segment_id",
                    (instance_id,),
                )
                segment_rows = list(cur.fetchall())
                if contacts_rows or segment_rows:
                    contacts = [
                        Contact(
                            email=email,
                            name=name,
                            audience=audience,
                            fields=json.loads(fields) if isinstance(fields, str) else (fields or {}),
                            tags=json.loads(tags) if isinstance(tags, str) else (tags or []),
                            active=active,
                        )
                        for email, name, audience, fields, tags, active in contacts_rows
                    ]
                    segments = [
                        Segment(
                            id=segment_id,
                            name=name,
                            match=json.loads(match) if isinstance(match, str) else (match or {}),
                            members=json.loads(members) if isinstance(members, str) else (members or []),
                        )
                        for segment_id, name, match, members in segment_rows
                    ]
                    return _normalize_config(ContactsConfig(contacts=contacts, segments=segments))
        raw = DEFAULT_CONTACTS_PATH.read_text() if DEFAULT_CONTACTS_PATH.is_file() else ""
        return _config_from_data(yaml.safe_load(raw) or {})

    file_path = _local_path(DEFAULT_CONTACTS_PATH, instance_id)
    raw = file_path.read_text() if file_path.is_file() else DEFAULT_CONTACTS_PATH.read_text() if DEFAULT_CONTACTS_PATH.is_file() else ""
    return _config_from_data(yaml.safe_load(raw) or {})


def dump_contacts(config: ContactsConfig) -> str:
    normalized = _normalize_config(config)
    return yaml.safe_dump(normalized.model_dump(), sort_keys=False, allow_unicode=True)


def save_contacts(config: ContactsConfig, path: str | Path | None = None, agent_instance_id: str | None = None) -> None:
    normalized = _normalize_config(config)
    if path is not None:
        Path(path).write_text(dump_contacts(normalized))
        return

    instance_id = normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())
    if settings.database_url:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM email_agent_contacts WHERE agent_instance_id = %s", (instance_id,))
                cur.execute("DELETE FROM email_agent_segments WHERE agent_instance_id = %s", (instance_id,))
                for contact in normalized.contacts:
                    cur.execute(
                        """
                        INSERT INTO email_agent_contacts (
                            agent_instance_id, email, name, audience, fields, tags, active, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, NOW())
                        """,
                        (
                            instance_id,
                            contact.email,
                            contact.name,
                            contact.audience,
                            json.dumps(contact.fields),
                            json.dumps(contact.tags),
                            contact.active,
                        ),
                    )
                for segment in normalized.segments:
                    cur.execute(
                        """
                        INSERT INTO email_agent_segments (
                            agent_instance_id, segment_id, name, match, members, updated_at
                        )
                        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, NOW())
                        """,
                        (
                            instance_id,
                            segment.id,
                            segment.name,
                            json.dumps(segment.match),
                            json.dumps(segment.members),
                        ),
                    )
        return

    file_path = _local_path(DEFAULT_CONTACTS_PATH, instance_id)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_name(file_path.name + ".tmp")
    temporary.write_text(dump_contacts(normalized))
    temporary.replace(file_path)


def list_contacts(agent_instance_id: str | None = None) -> list[Contact]:
    return load_contacts(agent_instance_id=agent_instance_id).contacts


def list_segments(agent_instance_id: str | None = None) -> list[Segment]:
    return load_contacts(agent_instance_id=agent_instance_id).segments


def _contact_path(contact: Contact, key: str):
    if key == "audience":
        return contact.audience
    if key == "name":
        return contact.name or ""
    if key == "active":
        return str(contact.active).lower()
    if key.startswith("fields."):
        return contact.fields.get(key.split(".", 1)[1], "")
    if key == "tags":
        return ",".join(contact.tags)
    return ""


def resolve_segment(segment: Segment, contacts: list[Contact] | None = None, agent_instance_id: str | None = None) -> list[Contact]:
    all_contacts = contacts if contacts is not None else load_contacts(agent_instance_id=agent_instance_id).contacts
    active_contacts = [contact for contact in all_contacts if contact.active]
    if segment.members:
        by_email = {contact.email: contact for contact in active_contacts}
        return [by_email[email] for email in segment.members if email in by_email]
    if not segment.match:
        return []
    resolved: list[Contact] = []
    for contact in active_contacts:
        matched = True
        for key, value in segment.match.items():
            if key == "tags":
                expected = {item.strip().lower() for item in str(value).split(",") if item.strip()}
                actual = {item.strip().lower() for item in contact.tags}
                if not expected.issubset(actual):
                    matched = False
                    break
                continue
            if str(_contact_path(contact, key)).strip().lower() != str(value).strip().lower():
                matched = False
                break
        if matched:
            resolved.append(contact)
    return resolved


def get_contact(email: str, agent_instance_id: str | None = None) -> Contact | None:
    normalized = str(email).strip().lower()
    return next((contact for contact in load_contacts(agent_instance_id=agent_instance_id).contacts if contact.email == normalized), None)


def create_contact(contact: Contact, agent_instance_id: str | None = None) -> Contact:
    config = load_contacts(agent_instance_id=agent_instance_id)
    if any(existing.email == contact.email for existing in config.contacts):
        raise ContactConflictError(f"contact '{contact.email}' already exists")
    config.contacts.append(contact)
    save_contacts(config, agent_instance_id=agent_instance_id)
    return contact


def update_contact(email: str, contact: Contact, agent_instance_id: str | None = None) -> Contact:
    normalized = str(email).strip().lower()
    config = load_contacts(agent_instance_id=agent_instance_id)
    if normalized != contact.email:
        raise ValueError("contact email in body must match the path")
    replaced = False
    next_contacts: list[Contact] = []
    for existing in config.contacts:
        if existing.email == normalized:
            next_contacts.append(contact)
            replaced = True
        else:
            next_contacts.append(existing)
    if not replaced:
        raise ContactNotFoundError(f"contact '{normalized}' not found")
    config.contacts = next_contacts
    save_contacts(config, agent_instance_id=agent_instance_id)
    return contact


def upsert_contact(contact: Contact, agent_instance_id: str | None = None) -> Contact:
    config = load_contacts(agent_instance_id=agent_instance_id)
    by_email = {existing.email: existing for existing in config.contacts}
    by_email[contact.email] = contact
    config.contacts = list(by_email.values())
    save_contacts(config, agent_instance_id=agent_instance_id)
    return contact


def delete_contact(email: str, agent_instance_id: str | None = None) -> None:
    normalized = str(email).strip().lower()
    config = load_contacts(agent_instance_id=agent_instance_id)
    next_contacts = [contact for contact in config.contacts if contact.email != normalized]
    if len(next_contacts) == len(config.contacts):
        raise ContactNotFoundError(f"contact '{normalized}' not found")
    config.contacts = next_contacts
    config.segments = [
        Segment(**(segment.model_dump() | {"members": [member for member in segment.members if member != normalized]}))
        for segment in config.segments
    ]
    save_contacts(config, agent_instance_id=agent_instance_id)


def get_segment(segment_id: str, agent_instance_id: str | None = None) -> Segment | None:
    normalized = str(segment_id).strip()
    return next((segment for segment in load_contacts(agent_instance_id=agent_instance_id).segments if segment.id == normalized), None)


def create_segment(segment: Segment, agent_instance_id: str | None = None) -> Segment:
    config = load_contacts(agent_instance_id=agent_instance_id)
    if any(existing.id == segment.id for existing in config.segments):
        raise SegmentConflictError(f"segment '{segment.id}' already exists")
    config.segments.append(segment)
    save_contacts(config, agent_instance_id=agent_instance_id)
    return segment


def update_segment(segment_id: str, segment: Segment, agent_instance_id: str | None = None) -> Segment:
    normalized = str(segment_id).strip()
    if normalized != segment.id:
        raise ValueError("segment id in body must match the path")
    config = load_contacts(agent_instance_id=agent_instance_id)
    replaced = False
    next_segments: list[Segment] = []
    for existing in config.segments:
        if existing.id == normalized:
            next_segments.append(segment)
            replaced = True
        else:
            next_segments.append(existing)
    if not replaced:
        raise SegmentNotFoundError(f"segment '{normalized}' not found")
    config.segments = next_segments
    save_contacts(config, agent_instance_id=agent_instance_id)
    return segment


def upsert_segment(segment: Segment, agent_instance_id: str | None = None) -> Segment:
    config = load_contacts(agent_instance_id=agent_instance_id)
    by_id = {existing.id: existing for existing in config.segments}
    by_id[segment.id] = segment
    config.segments = list(by_id.values())
    save_contacts(config, agent_instance_id=agent_instance_id)
    return segment


def delete_segment(segment_id: str, agent_instance_id: str | None = None) -> None:
    normalized = str(segment_id).strip()
    config = load_contacts(agent_instance_id=agent_instance_id)
    next_segments = [segment for segment in config.segments if segment.id != normalized]
    if len(next_segments) == len(config.segments):
        raise SegmentNotFoundError(f"segment '{normalized}' not found")
    config.segments = next_segments
    save_contacts(config, agent_instance_id=agent_instance_id)


def _csv_bool(value: str) -> bool:
    cleaned = str(value or "").strip().lower()
    return cleaned not in {"0", "false", "no", "non", "inactive"}


def import_contacts_csv(csv_text: str, audience_default: str = "client", agent_instance_id: str | None = None) -> dict:
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    if not reader.fieldnames or "email" not in {name.strip().lower() for name in reader.fieldnames if name}:
        raise ValueError("CSV must include an email column")

    config = load_contacts(agent_instance_id=agent_instance_id)
    by_email = {contact.email: contact for contact in config.contacts}
    imported: list[Contact] = []
    rejected: list[dict] = []

    for row_number, row in enumerate(reader, start=2):
        normalized_row = {str(key).strip().lower(): (value or "").strip() for key, value in row.items() if key is not None}
        email = normalized_row.get("email", "")
        audience = normalized_row.get("audience") or audience_default
        tags_raw = normalized_row.get("tags", "")
        fields: dict[str, str] = {}
        for key, value in normalized_row.items():
            if key in {"email", "name", "audience", "tags", "active"} or not value:
                continue
            field_key = key.split(".", 1)[1] if key.startswith("fields.") else key
            fields[field_key] = value
        try:
            contact = Contact(
                email=email,
                name=normalized_row.get("name") or None,
                audience=audience,
                fields=fields,
                tags=[item.strip() for item in tags_raw.split(",") if item.strip()],
                active=_csv_bool(normalized_row.get("active", "true")),
            )
        except ValidationError as exc:
            rejected.append({"row": row_number, "email": email or None, "reason": exc.errors()[0]["msg"]})
            continue
        by_email[contact.email] = contact
        imported.append(contact)

    config.contacts = list(by_email.values())
    save_contacts(config, agent_instance_id=agent_instance_id)
    return {
        "imported": imported,
        "rejected": rejected,
        "imported_count": len(imported),
        "rejected_count": len(rejected),
    }
