from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from src.config import SERVICE_ROOT, settings
from src.media_storage import get_backend
from src.tenant import current_agent_instance_id, normalize_agent_instance_id

MAX_IMAGE_BYTES = 200 * 1024
SIGNATURE_IMAGE_STEM = "signature-image"
SIGNATURE_IMAGE_MAX_WIDTH = 300
SIGNATURE_CID = "agora-signature-img"
CONTACT_PHOTO_SIZE = 128

_ALLOWED_FORMATS = {"PNG": "png", "JPEG": "jpg"}


@dataclass(frozen=True)
class StoredMedia:
    """Backend-agnostic handle to a saved media object (local disk or S3)."""

    key: str
    extension: str
    size: int


def media_root() -> Path:
    root = Path(settings.media_dir)
    if not root.is_absolute():
        root = SERVICE_ROOT / root
    return root


def _backend():
    return get_backend(local_root=media_root())


def _instance_id(agent_instance_id: str | None = None) -> str:
    return normalize_agent_instance_id(agent_instance_id or current_agent_instance_id())


def instance_media_dir(agent_instance_id: str | None = None) -> Path:
    """Local-disk path for this instance's media. Only meaningful for the local backend."""
    return media_root() / _instance_id(agent_instance_id)


def _signature_key(instance_id: str, extension: str) -> str:
    return f"{instance_id}/{SIGNATURE_IMAGE_STEM}.{extension}"


def find_signature_image(agent_instance_id: str | None = None) -> StoredMedia | None:
    instance_id = _instance_id(agent_instance_id)
    backend = _backend()
    for extension in _ALLOWED_FORMATS.values():
        key = _signature_key(instance_id, extension)
        size = backend.stat(key)
        if size is not None:
            return StoredMedia(key=key, extension=extension, size=size)
    return None


def _load_validated_image(data: bytes):
    from PIL import Image, UnidentifiedImageError

    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"Image trop lourde ({len(data) // 1024} Ko) — maximum {MAX_IMAGE_BYTES // 1024} Ko."
        )
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except UnidentifiedImageError as exc:
        raise ValueError("Fichier illisible — formats acceptés : PNG ou JPEG.") from exc
    if image.format not in _ALLOWED_FORMATS:
        raise ValueError("Format non pris en charge — formats acceptés : PNG ou JPEG.")
    return image


def save_signature_image(data: bytes, agent_instance_id: str | None = None) -> StoredMedia:
    """Validate, resize (≤300px wide) and store the instance signature image."""
    image = _load_validated_image(data)
    image_format = image.format
    if image.width > SIGNATURE_IMAGE_MAX_WIDTH:
        ratio = SIGNATURE_IMAGE_MAX_WIDTH / image.width
        image = image.resize(
            (SIGNATURE_IMAGE_MAX_WIDTH, max(1, round(image.height * ratio)))
        )
    delete_signature_image(agent_instance_id)
    instance_id = _instance_id(agent_instance_id)
    extension = _ALLOWED_FORMATS[image_format]
    key = _signature_key(instance_id, extension)
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    payload = buffer.getvalue()
    _backend().write(key, payload)
    return StoredMedia(key=key, extension=extension, size=len(payload))


def delete_signature_image(agent_instance_id: str | None = None) -> bool:
    existing = find_signature_image(agent_instance_id)
    if existing is None:
        return False
    return _backend().delete(existing.key)


def signature_image_inline(agent_instance_id: str | None = None) -> tuple[bytes, str] | None:
    """The stored signature image as (bytes, mime subtype) for cid embedding."""
    existing = find_signature_image(agent_instance_id)
    if existing is None:
        return None
    data = _backend().read(existing.key)
    if data is None:
        return None
    subtype = "png" if existing.extension == "png" else "jpeg"
    return data, subtype


# --- Contact photos: key derived from (instance, email); no DB column needed. ---


def _contact_photo_name(email: str) -> str:
    digest = hashlib.sha1(email.strip().lower().encode()).hexdigest()[:16]
    return f"{digest}.jpg"


def contact_photo_path(email: str, agent_instance_id: str | None = None) -> str:
    instance_id = _instance_id(agent_instance_id)
    return f"{instance_id}/contact-photos/{_contact_photo_name(email)}"


def find_contact_photo(email: str, agent_instance_id: str | None = None) -> StoredMedia | None:
    key = contact_photo_path(email, agent_instance_id)
    size = _backend().stat(key)
    if size is None:
        return None
    return StoredMedia(key=key, extension="jpg", size=size)


def save_contact_photo(email: str, data: bytes, agent_instance_id: str | None = None) -> StoredMedia:
    """Validate, center-crop to a 128px square JPEG and store the contact photo."""
    image = _load_validated_image(data)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    # Center-crop to a square, then resize to CONTACT_PHOTO_SIZE.
    side = min(image.width, image.height)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    if side != CONTACT_PHOTO_SIZE:
        image = image.resize((CONTACT_PHOTO_SIZE, CONTACT_PHOTO_SIZE))
    key = contact_photo_path(email, agent_instance_id)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    payload = buffer.getvalue()
    _backend().write(key, payload)
    return StoredMedia(key=key, extension="jpg", size=len(payload))


def delete_contact_photo(email: str, agent_instance_id: str | None = None) -> bool:
    key = contact_photo_path(email, agent_instance_id)
    return _backend().delete(key)


def read_contact_photo(email: str, agent_instance_id: str | None = None) -> bytes | None:
    key = contact_photo_path(email, agent_instance_id)
    return _backend().read(key)
