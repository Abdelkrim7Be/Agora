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
from src.image_fetch import (
    ImageFetchError,
    fetch_image_bytes,
)
from src.media import (
    delete_signature_image,
    save_signature_image,
    signature_image_inline,
)
from src.signature import (
    SIGNATURE_MODES,
    SignatureConfig,
    apply_signature,
    load_signature,
    save_signature,
)
from src.tenant import current_agent_instance_id
from src.api_shared import (
    _require_instance_role,
)

from pydantic import BaseModel

router = APIRouter()


class SignatureApplyInput(BaseModel):
    content: str
    mode: str


class SignatureImageUrlInput(BaseModel):
    url: str

@router.get("/signature")
async def get_signature() -> dict:
    signature = load_signature()
    return {
        "agent_instance_id": current_agent_instance_id(),
        **signature.model_dump(),
        "available_modes": list(SIGNATURE_MODES),
    }


@router.put("/signature")
async def update_signature(body: SignatureConfig) -> dict:
    if body.mode not in SIGNATURE_MODES:
        raise HTTPException(status_code=422, detail=f"mode must be one of: {', '.join(SIGNATURE_MODES)}")
    save_signature(body)
    return {
        "agent_instance_id": current_agent_instance_id(),
        **body.model_dump(),
        "available_modes": list(SIGNATURE_MODES),
    }


@router.post("/signature/apply")
async def apply_signature_endpoint(body: SignatureApplyInput) -> dict:
    """Compose the final body for one draft under an explicit mode override.

    Used by the approval UI's ask_each_time per-draft toggle: the chosen mode
    is applied here, and the already-signed content is then sent back through
    the normal 'edit' approval path — apply_signature's idempotency guarantee
    means the graph's own signature step (which runs with no override once
    signature.mode is ask_each_time) leaves this content untouched.
    """
    if body.mode not in SIGNATURE_MODES:
        raise HTTPException(status_code=422, detail=f"mode must be one of: {', '.join(SIGNATURE_MODES)}")
    signature = load_signature()
    return {"content": apply_signature(body.content, signature, mode=body.mode)}


@router.post("/signature/image")
async def upload_signature_image(request: Request, file: UploadFile = File(...)) -> dict:
    _require_instance_role(request, "owner")
    data = await file.read()
    try:
        stored = await asyncio.to_thread(save_signature_image, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "stored": True,
        "filename": stored.key.rsplit("/", 1)[-1],
        "size": stored.size,
    }


@router.post("/signature/image/from-url")
async def import_signature_image(request: Request, body: SignatureImageUrlInput) -> dict:
    """Store a signature image given its address.

    A URL left as a URL renders as a remote `<img>`, which most mail clients
    block — so the logo the owner chose never appeared for the recipient.
    Fetching it once here turns it into the same inline `cid:` part an upload
    produces, which always displays.

    See `src/image_fetch.py` for why this refuses non-public addresses.
    """
    _require_instance_role(request, "owner")
    try:
        data = await asyncio.to_thread(fetch_image_bytes, body.url)
        stored = await asyncio.to_thread(save_signature_image, data)
    except (ImageFetchError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "agent_instance_id": current_agent_instance_id(),
        "stored": True,
        "filename": stored.key.rsplit("/", 1)[-1],
        "size": stored.size,
        "source_url": body.url,
    }


@router.get("/signature/image")
async def get_signature_image() -> Response:
    found = await asyncio.to_thread(signature_image_inline)
    if found is None:
        raise HTTPException(status_code=404, detail="No signature image for this instance")
    data, subtype = found
    return Response(content=data, media_type=f"image/{subtype}")


@router.delete("/signature/image")
async def remove_signature_image(request: Request) -> dict:
    _require_instance_role(request, "owner")
    removed = delete_signature_image()
    return {"agent_instance_id": current_agent_instance_id(), "removed": removed}
