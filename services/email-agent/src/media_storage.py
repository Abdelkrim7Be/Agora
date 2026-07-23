from __future__ import annotations

from pathlib import Path
from typing import Protocol

from src.config import settings

MEDIA_BACKENDS = {"local", "s3"}


class MediaBackend(Protocol):
    def write(self, key: str, data: bytes) -> None: ...

    def read(self, key: str) -> bytes | None: ...

    def delete(self, key: str) -> bool: ...

    def stat(self, key: str) -> int | None:
        """Byte size of the stored object, or None if it doesn't exist."""
        ...


def selected_media_backend() -> str:
    backend = settings.media_backend.lower().strip()
    if backend not in MEDIA_BACKENDS:
        raise RuntimeError(f"Unsupported AGENT_MEDIA_BACKEND: {settings.media_backend}")
    if backend == "s3" and not settings.media_s3_bucket:
        raise RuntimeError("AGENT_MEDIA_S3_BUCKET is required when AGENT_MEDIA_BACKEND=s3")
    return backend


class LocalMediaBackend:
    """Plain filesystem storage under a root directory — today's behavior."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    def write(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def delete(self, key: str) -> bool:
        path = self._path(key)
        existed = path.is_file()
        path.unlink(missing_ok=True)
        return existed

    def stat(self, key: str) -> int | None:
        path = self._path(key)
        return path.stat().st_size if path.is_file() else None


def _s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("S3 media storage requires the boto3 package.") from exc
    return boto3.client(
        "s3",
        endpoint_url=settings.media_s3_endpoint_url or None,
        region_name=settings.media_s3_region or None,
    )


class S3MediaBackend:
    """S3-compatible object storage (OVH Object Storage, MinIO, AWS S3, ...)."""

    def __init__(self, bucket: str, client) -> None:
        self.bucket = bucket
        self.client = client

    @staticmethod
    def _is_not_found(exc) -> bool:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        return code in ("NoSuchKey", "404", "NotFound")

    def write(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def read(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if self._is_not_found(exc):
                return None
            raise

    def delete(self, key: str) -> bool:
        existed = self.stat(key) is not None
        if existed:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        return existed

    def stat(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
            return response["ContentLength"]
        except ClientError as exc:
            if self._is_not_found(exc):
                return None
            raise


def get_backend(local_root: Path) -> MediaBackend:
    backend = selected_media_backend()
    if backend == "local":
        return LocalMediaBackend(local_root)
    return S3MediaBackend(settings.media_s3_bucket, _s3_client())
