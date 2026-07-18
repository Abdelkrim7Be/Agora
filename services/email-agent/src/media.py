from __future__ import annotations

import io
from pathlib import Path

from src.config import SERVICE_ROOT, settings
from src.tenant import current_agent_instance_id, normalize_agent_instance_id

MAX_IMAGE_BYTES = 200 * 1024
SIGNATURE_IMAGE_STEM = "signature-image"
SIGNATURE_IMAGE_MAX_WIDTH = 300
SIGNATURE_CID = "agora-signature-img"

_ALLOWED_FORMATS = {"PNG": "png", "JPEG": "jpg"}


def media_root() -> Path:
    root = Path(settings.media_dir)
    if not root.is_absolute():
        root = SERVICE_ROOT / root
    return root


def instance_media_dir(agent_instance_id: str | None = None) -> Path:
    instance_id = normalize_agent_instance_id(
        agent_instance_id or current_agent_instance_id()
    )
    return media_root() / instance_id


def find_signature_image(agent_instance_id: str | None = None) -> Path | None:
    directory = instance_media_dir(agent_instance_id)
    if not directory.is_dir():
        return None
    for extension in _ALLOWED_FORMATS.values():
        candidate = directory / f"{SIGNATURE_IMAGE_STEM}.{extension}"
        if candidate.is_file():
            return candidate
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


def save_signature_image(data: bytes, agent_instance_id: str | None = None) -> Path:
    """Validate, resize (≤300px wide) and store the instance signature image."""
    image = _load_validated_image(data)
    image_format = image.format
    if image.width > SIGNATURE_IMAGE_MAX_WIDTH:
        ratio = SIGNATURE_IMAGE_MAX_WIDTH / image.width
        image = image.resize(
            (SIGNATURE_IMAGE_MAX_WIDTH, max(1, round(image.height * ratio)))
        )
    delete_signature_image(agent_instance_id)
    directory = instance_media_dir(agent_instance_id)
    directory.mkdir(parents=True, exist_ok=True)
    extension = _ALLOWED_FORMATS[image_format]
    path = directory / f"{SIGNATURE_IMAGE_STEM}.{extension}"
    image.save(path, format=image_format)
    return path


def delete_signature_image(agent_instance_id: str | None = None) -> bool:
    existing = find_signature_image(agent_instance_id)
    if existing is None:
        return False
    existing.unlink(missing_ok=True)
    return True


def signature_image_inline(agent_instance_id: str | None = None) -> tuple[bytes, str] | None:
    """The stored signature image as (bytes, mime subtype) for cid embedding."""
    path = find_signature_image(agent_instance_id)
    if path is None:
        return None
    subtype = "png" if path.suffix == ".png" else "jpeg"
    return path.read_bytes(), subtype
