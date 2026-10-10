"""Ingest gate: hard caps raise IngestError; soft notes returned separately."""

from app.media.errors import IngestError
from app.media.models import MediaInfo

MAX_VIDEO_SECONDS = 90
MAX_UPLOAD_BYTES = 1_000_000_000  # 1 GB single PUT is fine (S3 limit 5 GB)
MAX_DIMENSION = 3840  # 4K ceiling either side
MAX_FPS = 120.0
# ffprobe format_name allowlist. Playlists (hls/mpd) and concat tricks are OUT:
# ffmpeg would fetch remote segments = SSRF (see hunt-file-upload review).
ALLOWED_CONTAINERS = frozenset(
    {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "avi"}
)


def validate_info(info: MediaInfo, max_seconds: int = MAX_VIDEO_SECONDS) -> None:
    """Raise IngestError on the first hard violation (fail fast at the gate)."""
    names = set(info.container.split(","))
    if not names & ALLOWED_CONTAINERS:
        raise IngestError(
            "container",
            f"container {info.container!r} is not an accepted upload format",
            "upload mp4/mov/mkv/webm/avi (playlists and stream manifests are rejected)",
        )
    duration_ms = round(info.duration_s * 1000)
    if duration_ms > max_seconds * 1000:
        raise IngestError(
            "too_long",
            f"video is {info.duration_s:.3f}s, over the {max_seconds}s cap",
            "trim it below 90 s before uploading",
        )
    if info.size_bytes > MAX_UPLOAD_BYTES:
        raise IngestError(
            "too_large",
            f"file is {info.size_bytes} bytes, over the 1 GB cap",
            "re-encode at a lower bitrate before uploading",
        )
    if info.width > MAX_DIMENSION or info.height > MAX_DIMENSION:
        raise IngestError(
            "resolution",
            f"frame {info.width}x{info.height} exceeds the 3840px ceiling",
            "downscale to 4K or below before uploading",
        )
    if info.fps > MAX_FPS:
        raise IngestError(
            "frame_rate",
            f"frame rate {info.fps}fps exceeds the 120fps ceiling",
            "convert to 120fps or below before uploading",
        )


def warnings(info: MediaInfo) -> list[str]:
    """Soft notes: accepted, but the pipeline should know."""
    notes: list[str] = []
    if info.acodec is None:
        notes.append("silent")
    if not info.is_cfr:
        notes.append("vfr")
    if info.rotation_deg:
        notes.append("rotated")
    if info.is_hdr:
        notes.append("hdr")
    return notes
