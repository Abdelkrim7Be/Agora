from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import src.media as media
from src.api import app
from src.gmail_client import _build_email_message
from src.media import SIGNATURE_CID, save_signature_image
from src.signature import SignatureConfig, append_signature


def _png_bytes(width: int = 400, height: int = 120) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(20, 90, 190)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def media_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(media.settings, "media_dir", str(tmp_path))
    return tmp_path


def test_save_signature_image_resizes_to_max_width(media_tmp):
    path = save_signature_image(_png_bytes(width=600, height=300))
    with Image.open(path) as stored:
        assert stored.width == 300
        assert stored.height == 150


def test_save_signature_image_keeps_small_images(media_tmp):
    path = save_signature_image(_png_bytes(width=120, height=40))
    with Image.open(path) as stored:
        assert stored.size == (120, 40)


def test_save_signature_image_rejects_oversize(media_tmp):
    with pytest.raises(ValueError, match="trop lourde"):
        save_signature_image(b"0" * (media.MAX_IMAGE_BYTES + 1))


def test_save_signature_image_rejects_non_image(media_tmp):
    with pytest.raises(ValueError, match="illisible"):
        save_signature_image(b"not an image at all")


def test_upload_get_delete_endpoints_round_trip(media_tmp):
    with TestClient(app) as client:
        upload = client.post(
            "/signature/image",
            files={"file": ("logo.png", _png_bytes(), "image/png")},
        )
        assert upload.status_code == 200
        assert upload.json()["stored"] is True

        fetched = client.get("/signature/image")
        assert fetched.status_code == 200
        assert fetched.headers["content-type"] == "image/png"

        removed = client.delete("/signature/image")
        assert removed.json()["removed"] is True
        assert client.get("/signature/image").status_code == 404


def test_upload_rejects_bad_file_with_400(media_tmp):
    with TestClient(app) as client:
        response = client.post(
            "/signature/image",
            files={"file": ("evil.txt", b"plain text", "text/plain")},
        )
    assert response.status_code == 400


def test_upload_requires_owner_role(media_tmp):
    with TestClient(app) as client:
        denied = client.post(
            "/signature/image",
            files={"file": ("logo.png", _png_bytes(), "image/png")},
            headers={"X-Agora-Instance-Role": "viewer"},
        )
    assert denied.status_code == 403


def test_signature_uses_cid_when_image_stored(media_tmp):
    save_signature_image(_png_bytes())
    signature = SignatureConfig(enabled=True, first_name="Karim")
    signed = append_signature("Bonjour", signature)
    assert f"cid:{SIGNATURE_CID}" in signed


def test_build_email_message_embeds_inline_image():
    data = _png_bytes(width=100)
    message = _build_email_message(
        "alice@example.com",
        "Re",
        f"Bonjour\n\n![Signature](cid:{SIGNATURE_CID})",
        inline_images={SIGNATURE_CID: (data, "png")},
    )
    related = [
        part for part in message.walk() if part.get("Content-ID") == f"<{SIGNATURE_CID}>"
    ]
    assert related, "expected an inline cid image part"
    assert related[0].get_content_type() == "image/png"
