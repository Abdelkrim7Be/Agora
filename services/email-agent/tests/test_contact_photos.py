from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import src.media as media
from src.api import app
from src.media import (
    CONTACT_PHOTO_SIZE,
    contact_photo_path,
    delete_contact_photo,
    find_contact_photo,
    save_contact_photo,
)


def _png_bytes(width: int = 300, height: int = 200) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(200, 80, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def media_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(media.settings, "media_dir", str(tmp_path))
    return tmp_path


def test_save_contact_photo_produces_square_jpeg(media_tmp):
    path = save_contact_photo("Alice@Example.com", _png_bytes(400, 200))
    assert path.suffix == ".jpg"
    with Image.open(path) as stored:
        assert stored.size == (CONTACT_PHOTO_SIZE, CONTACT_PHOTO_SIZE)
        assert stored.format == "JPEG"


def test_contact_photo_path_is_case_insensitive(media_tmp):
    assert contact_photo_path("Alice@Example.com") == contact_photo_path("alice@example.com")


def test_find_and_delete_contact_photo(media_tmp):
    assert find_contact_photo("bob@example.com") is None
    save_contact_photo("bob@example.com", _png_bytes())
    assert find_contact_photo("bob@example.com") is not None
    assert delete_contact_photo("bob@example.com") is True
    assert find_contact_photo("bob@example.com") is None


def test_save_contact_photo_rejects_non_image(media_tmp):
    with pytest.raises(ValueError):
        save_contact_photo("x@example.com", b"not an image")


def test_contact_photo_endpoints_round_trip(media_tmp):
    with TestClient(app) as client:
        upload = client.post(
            "/contacts/alice@example.com/photo",
            files={"file": ("a.png", _png_bytes(), "image/png")},
        )
        assert upload.status_code == 200
        assert upload.json()["stored"] is True

        fetched = client.get("/contacts/alice@example.com/photo")
        assert fetched.status_code == 200
        assert fetched.headers["content-type"] == "image/jpeg"

        removed = client.delete("/contacts/alice@example.com/photo")
        assert removed.json()["removed"] is True
        assert client.get("/contacts/alice@example.com/photo").status_code == 404


def test_contact_photo_upload_requires_owner(media_tmp):
    with TestClient(app) as client:
        denied = client.post(
            "/contacts/alice@example.com/photo",
            files={"file": ("a.png", _png_bytes(), "image/png")},
            headers={"X-Agora-Instance-Role": "viewer"},
        )
    assert denied.status_code == 403
