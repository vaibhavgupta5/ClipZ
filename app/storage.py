"""Local-filesystem storage (ADR-010). Keys are routed to three roots by prefix:

    uploads/<project>/...   -> UPLOAD_DIR   raw uploads, deleted after ingest
    renders/<project>/...   -> EDITED_DIR   previews + exports (kept)
    projects/<project>/...  -> WORK_DIR/<project>/  master, proxy, ASR audio (deleted after export)
    anything else           -> WORK_DIR/cache/      content-hashed caches (segments, analysis)

Keys are validated (no traversal, no absolute paths) and every resolved path is
re-checked to stay inside its root.
"""

from pathlib import Path
from typing import Protocol

from app.settings import Settings

MAX_KEY_LENGTH = 1024


def validate_key(key: str) -> str:
    """Reject unsafe storage keys (traversal, absolute, empty, too long)."""
    if (
        not key
        or len(key) > MAX_KEY_LENGTH
        or key.startswith("/")
        or "\\" in key
        or "\x00" in key
        or ".." in key.split("/")
    ):
        raise ValueError(f"unsafe storage key: {key!r}")
    return key


class Storage(Protocol):
    """Storage contract. Implementations: LocalStorage (real), S3Storage (cloud), MemoryStorage (tests)."""

    def ensure_dirs(self) -> None: ...
    def path_for(self, key: str) -> Path: ...
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def stat(self, key: str) -> int:
        """Size in bytes. Raises KeyError when missing."""
        ...

    def public_url(self, key: str) -> str:
        """URL path the API serves the object at (GET /media/<key> or public S3 CDN URL)."""
        ...

    def delete_tree(self, prefix: str) -> None: ...
    def writable(self) -> bool: ...


def media_url(key: str) -> str:
    """API path for a stored object."""
    return f"/media/{validate_key(key)}"


class MemoryStorage:
    """In-memory Storage fake for unit tests."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def ensure_dirs(self) -> None:
        pass

    def path_for(self, key: str) -> Path:
        return Path(f"/tmp/memory_storage/{validate_key(key)}")

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

    def public_url(self, key: str) -> str:
        return media_url(key)

    def delete_tree(self, prefix: str) -> None:
        prefix_clean = validate_key(prefix)
        keys_to_del = [k for k in self._blobs if k == prefix_clean or k.startswith(prefix_clean + "/")]
        for k in keys_to_del:
            self._blobs.pop(k, None)

    def writable(self) -> bool:
        return True


class LocalStorage:
    """Filesystem Storage over upload/, work/ and editedVideo/ roots."""

    def __init__(self, upload_dir: str, work_dir: str, edited_dir: str) -> None:
        self.upload = Path(upload_dir).resolve()
        self.work = Path(work_dir).resolve()
        self.edited = Path(edited_dir).resolve()

    def ensure_dirs(self) -> None:
        """Create the three roots (idempotent)."""
        for root in (self.upload, self.work / "cache", self.edited):
            root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        """Absolute path for a key, guaranteed inside its root."""
        validate_key(key)
        head, _, rest = key.partition("/")
        if head == "uploads":
            root, rel = self.upload, rest
        elif head == "renders":
            root, rel = self.edited, rest
        elif head == "projects":
            root, rel = self.work, rest
        else:
            root, rel = self.work / "cache", key
        if not rel:
            raise ValueError(f"storage key has no object name: {key!r}")
        path = (root / rel).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"storage key escapes its root: {key!r}")
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic within a filesystem

    def get_bytes(self, key: str) -> bytes:
        path = self.path_for(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise KeyError(f"object not found: {key!r}") from None

    def delete(self, key: str) -> None:
        self.path_for(key).unlink(missing_ok=True)

    def stat(self, key: str) -> int:
        try:
            return self.path_for(key).stat().st_size
        except FileNotFoundError:
            raise KeyError(f"object not found: {key!r}") from None

    def public_url(self, key: str) -> str:
        return media_url(key)

    def delete_tree(self, prefix: str) -> None:
        """Remove every object under ``uploads/<p>`` or ``projects/<p>`` (lifecycle cleanup)."""
        import shutil

        if prefix.split("/")[0] not in ("uploads", "projects") or prefix.count("/") != 1:
            raise ValueError(f"delete_tree only takes uploads/<id> or projects/<id>: {prefix!r}")
        path = self.path_for(prefix + "/x").parent
        shutil.rmtree(path, ignore_errors=True)

    def writable(self) -> bool:
        """Readiness check: all roots exist and are writable."""
        try:
            self.ensure_dirs()
            probe = self.work / "cache" / ".readyz"
            probe.write_bytes(b"ok")
            probe.unlink()
            return True
        except OSError:
            return False


class S3Storage:
    """S3-compatible Object Storage (DigitalOcean Spaces, AWS S3, Cloudflare R2, MinIO)
    with local disk caching for FFmpeg rendering and processing."""

    def __init__(
        self,
        bucket_name: str,
        endpoint_url: str | None = None,
        region_name: str = "us-east-1",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        public_url_base: str | None = None,
        upload_dir: str = "./upload",
        work_dir: str = "./work",
        edited_dir: str = "./editedVideo",
    ) -> None:
        from app.providers.s3 import S3Client

        self.bucket = bucket_name
        self.public_url_base = public_url_base.rstrip("/") if public_url_base else None
        self.local = LocalStorage(upload_dir, work_dir, edited_dir)
        self.s3 = S3Client(
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )

    def ensure_dirs(self) -> None:
        self.local.ensure_dirs()

    def path_for(self, key: str) -> Path:
        """Absolute local path for FFmpeg/Whisper. Downloads from S3 if not cached locally."""
        validate_key(key)
        path = self.local.path_for(key)
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".s3download")
            try:
                self.s3.download_file(key, str(tmp))
                tmp.replace(path)
            except Exception as exc:
                if tmp.is_file():
                    tmp.unlink(missing_ok=True)
                raise KeyError(f"object not found in s3: {key!r} ({exc})") from None
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        validate_key(key)
        self.local.put_bytes(key, data, content_type)
        self.s3.put_object(key, data, content_type)

    def get_bytes(self, key: str) -> bytes:
        validate_key(key)
        try:
            return self.local.get_bytes(key)
        except KeyError:
            pass
        try:
            data, content_type = self.s3.get_object(key)
            self.local.put_bytes(key, data, content_type)
            return data
        except Exception:
            raise KeyError(f"object not found: {key!r}") from None

    def delete(self, key: str) -> None:
        validate_key(key)
        self.local.delete(key)
        try:
            self.s3.delete_object(key)
        except Exception:
            pass

    def stat(self, key: str) -> int:
        validate_key(key)
        try:
            return self.local.stat(key)
        except KeyError:
            pass
        try:
            resp = self.s3.head_object(key)
            return int(resp["ContentLength"])
        except Exception:
            raise KeyError(f"object not found: {key!r}") from None

    def public_url(self, key: str) -> str:
        validate_key(key)
        if self.public_url_base:
            return f"{self.public_url_base}/{key}"
        return media_url(key)

    def delete_tree(self, prefix: str) -> None:
        self.local.delete_tree(prefix)
        try:
            self.s3.delete_prefix(prefix)
        except Exception:
            pass

    def writable(self) -> bool:
        if not self.local.writable():
            return False
        try:
            return self.s3.head_bucket()
        except Exception:
            return False


def get_storage(settings: Settings) -> Storage:
    """Build the configured Storage (LocalStorage or S3Storage) from settings."""
    if settings.STORAGE_BACKEND == "s3" and settings.S3_BUCKET_NAME:
        return S3Storage(
            bucket_name=settings.S3_BUCKET_NAME,
            endpoint_url=settings.S3_ENDPOINT_URL or None,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY or None,
            public_url_base=settings.S3_PUBLIC_URL_BASE or None,
            upload_dir=settings.UPLOAD_DIR,
            work_dir=settings.WORK_DIR,
            edited_dir=settings.EDITED_DIR,
        )
    return LocalStorage(settings.UPLOAD_DIR, settings.WORK_DIR, settings.EDITED_DIR)
