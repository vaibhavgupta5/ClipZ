"""S3-compatible object storage abstraction (MinIO local, R2/S3 prod)."""

from typing import Protocol

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from app.settings import Settings

MAX_KEY_LENGTH = 1024


def validate_key(key: str) -> str:
    """Reject unsafe object keys (traversal, absolute, empty, too long)."""
    if not key or len(key) > MAX_KEY_LENGTH or key.startswith("/") or ".." in key.split("/"):
        raise ValueError(f"unsafe storage key: {key!r}")
    return key


class Storage(Protocol):
    """Object-storage contract. Implementations: S3Storage (real), MemoryStorage (tests)."""

    def presigned_upload_url(self, key: str, content_type: str, expires_s: int = 3600) -> str: ...
    def presigned_download_url(self, key: str, expires_s: int = 3600) -> str: ...
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def stat(self, key: str) -> int:
        """Size in bytes. Raises KeyError when missing."""
        ...


class MemoryStorage:
    """In-memory Storage fake for unit tests (no network)."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def presigned_upload_url(self, key: str, content_type: str, expires_s: int = 3600) -> str:
        return f"memory://{validate_key(key)}"

    def presigned_download_url(self, key: str, expires_s: int = 3600) -> str:
        return f"memory://{validate_key(key)}"

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._blobs[validate_key(key)] = data

    def get_bytes(self, key: str) -> bytes:
        try:
            return self._blobs[validate_key(key)]
        except KeyError:
            raise KeyError(f"object not found: {key!r}") from None

    def delete(self, key: str) -> None:
        self._blobs.pop(validate_key(key), None)

    def stat(self, key: str) -> int:
        try:
            return len(self._blobs[validate_key(key)])
        except KeyError:
            raise KeyError(f"object not found: {key!r}") from None


class S3Storage:
    """boto3-backed Storage for any S3-compatible endpoint."""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        client: BaseClient | None = None,
        public_endpoint: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self._client = client
        self._public_endpoint = public_endpoint or endpoint
        self._presign_client: BaseClient | None = None

    def _get_client(self) -> BaseClient:
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
        return self._client

    def _get_presign_client(self) -> BaseClient:
        """Signing-only client bound to the PUBLIC endpoint (no requests ever made)."""
        if self._presign_client is None:
            self._presign_client = boto3.client(
                "s3",
                endpoint_url=self._public_endpoint,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            )
        return self._presign_client

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not exist (idempotent)."""
        client = self._get_client()
        try:
            client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                raise
            client.create_bucket(Bucket=self.bucket)

    def bucket_exists(self) -> bool:
        """Cheap connectivity + existence check for /readyz."""
        try:
            self._get_client().head_bucket(Bucket=self.bucket)
            return True
        except ClientError:
            return False

    def presigned_upload_url(self, key: str, content_type: str, expires_s: int = 3600) -> str:
        validate_key(key)
        return str(
            self._get_presign_client().generate_presigned_url(
                "put_object",
                Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
                ExpiresIn=expires_s,
            )
        )

    def presigned_download_url(self, key: str, expires_s: int = 3600) -> str:
        validate_key(key)
        return str(
            self._get_presign_client().generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_s
            )
        )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        validate_key(key)
        self._get_client().put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    def get_bytes(self, key: str) -> bytes:
        validate_key(key)
        try:
            resp = self._get_client().get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                raise KeyError(f"object not found: {key!r}") from None
            raise
        return bytes(resp["Body"].read())

    def delete(self, key: str) -> None:
        validate_key(key)
        self._get_client().delete_object(Bucket=self.bucket, Key=key)

    def stat(self, key: str) -> int:
        validate_key(key)
        try:
            resp = self._get_client().head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                raise KeyError(f"object not found: {key!r}") from None
            raise
        return int(resp["ContentLength"])


def get_storage(settings: Settings) -> S3Storage:
    """Build the configured S3Storage from settings."""
    return S3Storage(
        endpoint=settings.S3_ENDPOINT,
        bucket=settings.S3_BUCKET,
        access_key=settings.S3_ACCESS_KEY,
        secret_key=settings.S3_SECRET_KEY,
        region=settings.S3_REGION,
        public_endpoint=settings.S3_PUBLIC_ENDPOINT,
    )
