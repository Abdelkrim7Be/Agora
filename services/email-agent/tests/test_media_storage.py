from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from src import media_storage
from src.media_storage import LocalMediaBackend, S3MediaBackend, selected_media_backend


def _not_found(code: str = "NoSuchKey") -> ClientError:
    return ClientError({"Error": {"Code": code}}, "GetObject")


class FakeS3Client:
    """Duck-typed stand-in for a boto3 S3 client, backed by an in-memory dict."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _not_found("NoSuchKey")
        return {"Body": _FakeBody(self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _not_found("404")
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def test_local_backend_round_trip(tmp_path):
    backend = LocalMediaBackend(tmp_path)
    assert backend.stat("a/b.png") is None
    assert backend.read("a/b.png") is None
    backend.write("a/b.png", b"hello")
    assert backend.stat("a/b.png") == 5
    assert backend.read("a/b.png") == b"hello"
    assert backend.delete("a/b.png") is True
    assert backend.delete("a/b.png") is False
    assert backend.read("a/b.png") is None


def test_s3_backend_round_trip():
    backend = S3MediaBackend("test-bucket", FakeS3Client())
    assert backend.stat("k") is None
    assert backend.read("k") is None
    backend.write("k", b"payload")
    assert backend.stat("k") == len(b"payload")
    assert backend.read("k") == b"payload"
    assert backend.delete("k") is True
    assert backend.delete("k") is False
    assert backend.read("k") is None


def test_s3_backend_reraises_non_not_found_errors():
    client = FakeS3Client()

    def _boom(Bucket, Key):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    client.get_object = _boom
    backend = S3MediaBackend("test-bucket", client)
    with pytest.raises(ClientError):
        backend.read("k")


def test_selected_media_backend_rejects_unsupported(monkeypatch):
    monkeypatch.setattr(media_storage.settings, "media_backend", "nope")
    with pytest.raises(RuntimeError, match="Unsupported AGENT_MEDIA_BACKEND"):
        selected_media_backend()


def test_selected_media_backend_requires_bucket_for_s3(monkeypatch):
    monkeypatch.setattr(media_storage.settings, "media_backend", "s3")
    monkeypatch.setattr(media_storage.settings, "media_s3_bucket", "")
    with pytest.raises(RuntimeError, match="AGENT_MEDIA_S3_BUCKET"):
        selected_media_backend()


def test_selected_media_backend_defaults_to_local():
    assert selected_media_backend() == "local"
