from __future__ import annotations

import asyncio

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import Response
from pydantic import (
    BaseModel,
    Field,
    field_validator,
)
from src.categories import (
    Contact as LegacyCategoryContact,
    DEFAULT_CATEGORIES_PATH,
    dump_categories,
    load_categories,
)
from src.contacts import (
    AUDIENCE_VALUES,
    Contact,
    ContactCategoryError,
    ContactConflictError,
    ContactNotFoundError,
    Segment,
    SegmentConflictError,
    SegmentNotFoundError,
    create_contact,
    create_segment,
    delete_contact,
    delete_segment,
    import_contacts_csv,
    list_contacts as list_directory_contacts,
    list_segments,
    update_contact,
    update_segment,
    upsert_contact,
)
from src.instance_config import write_instance_text
from src.media import (
    delete_contact_photo,
    read_contact_photo,
    save_contact_photo,
)
from src.tenant import current_agent_instance_id
from src.api_shared import (
    _require_instance_role,
    _serialize_contact,
    _serialize_segment,
)

router = APIRouter()

class ContactInput(BaseModel):
    email: str
    name: str | None = None
    # Validated here, not only on the domain `Contact`. An unknown audience used
    # to pass this model as a free string and then raise inside the handler,
    # which FastAPI turns into a 500 — a client mistake reported as a server
    # fault, with no indication of the accepted values. Kept as a validator
    # rather than a Literal so the domain model's normalization still applies:
    # "Client " remains valid input.
    audience: str
    fields: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    active: bool = True
    category: str | None = None
    domain: str | None = None
    priority: str | None = None
    category_source: str = "manual"
    category_confidence: float | None = None

    @field_validator("audience")
    @classmethod
    def _known_audience(cls, value: str) -> str:
        cleaned = str(value).strip().lower()
        if cleaned not in AUDIENCE_VALUES:
            raise ValueError(f"audience must be one of: {', '.join(AUDIENCE_VALUES)}")
        return cleaned


class SegmentInput(BaseModel):
    id: str
    name: str
    match: dict[str, str] = Field(default_factory=dict)
    members: list[str] = Field(default_factory=list)


class ContactsImportInput(BaseModel):
    csv_text: str
    audience_default: str = "client"


class CategorizeContactInput(BaseModel):
    email: str
    category: str
    domain_only: bool = False


def _contact_from_input(body: ContactInput) -> Contact:
    return Contact(
        email=body.email,
        name=body.name,
        audience=body.audience,
        fields=body.fields,
        tags=body.tags,
        active=body.active,
        category=body.category,
        domain=body.domain,
        priority=body.priority,
        category_source=body.category_source,
        category_confidence=body.category_confidence,
    )


def _segment_from_input(body: SegmentInput) -> Segment:
    return Segment(id=body.id, name=body.name, match=body.match, members=body.members)


@router.get("/contacts")
async def get_contacts(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    contacts = list_directory_contacts()
    return {
        "agent_instance_id": current_agent_instance_id(),
        "contacts": [_serialize_contact(contact) for contact in contacts],
        "storage": "contacts-directory",
    }


@router.post("/contacts", status_code=201)
async def create_contact_entry(request: Request, body: ContactInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        contact = create_contact(_contact_from_input(body))
    except ContactConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ContactCategoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "contact": _serialize_contact(contact),
        "storage": "contacts-directory",
    }


@router.put("/contacts/{email}")
async def update_contact_entry(email: str, request: Request, body: ContactInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        contact = update_contact(email, _contact_from_input(body))
    except ContactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ContactCategoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "contact": _serialize_contact(contact),
        "storage": "contacts-directory",
    }


@router.delete("/contacts/{email}")
async def delete_contact_entry(email: str, request: Request) -> dict:
    _require_instance_role(request, "owner")
    try:
        delete_contact(email)
    except ContactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "deleted": email.strip().lower(),
        "storage": "contacts-directory",
    }


@router.post("/contacts/{email}/photo")
async def upload_contact_photo(email: str, request: Request, file: UploadFile = File(...)) -> dict:
    _require_instance_role(request, "owner")
    data = await file.read()
    try:
        stored = await asyncio.to_thread(save_contact_photo, email, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "email": email.strip().lower(),
        "stored": True,
        "size": stored.size,
    }


@router.get("/contacts/{email}/photo")
async def get_contact_photo(email: str) -> Response:
    data = await asyncio.to_thread(read_contact_photo, email)
    if data is None:
        raise HTTPException(status_code=404, detail="No photo for this contact")
    return Response(content=data, media_type="image/jpeg")


@router.delete("/contacts/{email}/photo")
async def remove_contact_photo(email: str, request: Request) -> dict:
    _require_instance_role(request, "owner")
    removed = delete_contact_photo(email)
    return {
        "agent_instance_id": current_agent_instance_id(),
        "email": email.strip().lower(),
        "removed": removed,
    }


@router.post("/contacts/import")
async def import_contacts_entries(request: Request, body: ContactsImportInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        result = import_contacts_csv(body.csv_text, audience_default=body.audience_default)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "imported": [_serialize_contact(contact) for contact in result["imported"]],
        "rejected": result["rejected"],
        "imported_count": result["imported_count"],
        "rejected_count": result["rejected_count"],
        "storage": "contacts-directory",
    }


@router.post("/contacts/migrate-legacy")
async def migrate_legacy_contacts_endpoint(request: Request) -> dict:
    _require_instance_role(request, "owner")
    from src.contacts import migrate_legacy_category_contacts

    result = await asyncio.to_thread(migrate_legacy_category_contacts)
    return {"agent_instance_id": current_agent_instance_id(), **result}


@router.post("/contacts/categorize")
async def categorize_contact_endpoint(request: Request, body: CategorizeContactInput) -> dict:
    """One-action 'categoriser l'expéditeur' / 'categoriser le domaine' from the inbox row.

    Sender-scoped requests upsert the unified contact directory (email always
    present there). Domain-only requests can't live in the directory (its email
    field is required) so they upsert a legacy categories.yaml domain contact
    instead — still picked up by classify_category's precedence-4 domain match.
    """
    _require_instance_role(request, "owner")
    from email.utils import parseaddr

    _, address = parseaddr(body.email)
    address = (address or body.email).strip().lower()
    if "@" not in address:
        raise HTTPException(status_code=400, detail="email must contain an address to categorize")

    if body.domain_only:
        domain = address.rsplit("@", 1)[-1]
        cfg = await asyncio.to_thread(load_categories, None, current_agent_instance_id())
        cfg.contacts = [c for c in cfg.contacts if not (c.domain and c.domain.lower() == domain and not c.email)]
        cfg.contacts.append(LegacyCategoryContact(domain=domain, category=body.category))
        write_instance_text("categories", dump_categories(cfg), DEFAULT_CATEGORIES_PATH)
        return {"agent_instance_id": current_agent_instance_id(), "domain": domain, "category": body.category}

    try:
        contact = upsert_contact(Contact(email=address, audience="prospect", category=body.category, category_source="manual"))
    except ContactCategoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"agent_instance_id": current_agent_instance_id(), "contact": _serialize_contact(contact)}


@router.get("/segments")
async def get_segments(request: Request) -> dict:
    _require_instance_role(request, "viewer")
    segments = list_segments()
    return {
        "agent_instance_id": current_agent_instance_id(),
        "segments": [_serialize_segment(segment) for segment in segments],
        "storage": "contacts-directory",
    }


@router.post("/segments", status_code=201)
async def create_segment_entry(request: Request, body: SegmentInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        segment = create_segment(_segment_from_input(body))
    except SegmentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "segment": _serialize_segment(segment),
        "storage": "contacts-directory",
    }


@router.put("/segments/{segment_id}")
async def update_segment_entry(segment_id: str, request: Request, body: SegmentInput) -> dict:
    _require_instance_role(request, "owner")
    try:
        segment = update_segment(segment_id, _segment_from_input(body))
    except SegmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "segment": _serialize_segment(segment),
        "storage": "contacts-directory",
    }


@router.delete("/segments/{segment_id}")
async def delete_segment_entry(segment_id: str, request: Request) -> dict:
    _require_instance_role(request, "owner")
    try:
        delete_segment(segment_id)
    except SegmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "deleted": segment_id.strip(),
        "storage": "contacts-directory",
    }
