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
    """Storage contract. Implementations: LocalStorage (real), MemoryStorage (tests)."""

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def stat(self, key: str) -> int:
        """Size in bytes. Raises KeyError when missing."""
        ...

    def public_url(self, key: str) -> str:
        """URL path the API serves the object at (GET /media/<key>)."""
        ...


def media_url(key: str) -> str:
    """API path for a stored object."""
    return f"/media/{validate_key(key)}"


class MemoryStorage:
    """In-memory Storage fake for unit tests."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

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


def get_storage(settings: Settings) -> LocalStorage:
    """Build the configured LocalStorage from settings."""
    return LocalStorage(settings.UPLOAD_DIR, settings.WORK_DIR, settings.EDITED_DIR)
