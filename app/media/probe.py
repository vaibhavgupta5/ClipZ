"""ffprobe wrapper: arg-list invocation, JSON parse into MediaInfo."""

import json
import shutil
import subprocess
from typing import Any

from app.media.errors import MediaError
from app.media.models import MediaInfo

PROBE_TIMEOUT_S = 60


def _parse_rate(value: str, what: str) -> float:
    try:
        num, den = value.split("/")
        rate = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        raise MediaError("unparseable", f"bad {what} {value!r}") from None
    if rate <= 0:
        raise MediaError("unparseable", f"non-positive {what} {value!r}")
    return rate


def _rotation_of(stream: dict[str, Any]) -> int:
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(tags["rotate"]) % 360
        except ValueError:
            pass
    for side in stream.get("side_data_list") or []:
        if side.get("side_data_type") == "Display Matrix" and "rotation" in side:
            try:
                return int(float(side["rotation"])) % 360
            except ValueError:
                pass
    return 0


def ffprobe_path() -> str:
    """Resolve the ffprobe binary (absolute path) or raise."""
    path = shutil.which("ffprobe")
    if path is None:
        raise MediaError("no_ffprobe", "ffprobe binary not found on PATH")
    return path


def parse_probe_json(doc: dict[str, Any], size_bytes: int) -> MediaInfo:
    """Pure parse of ffprobe -of json output. Raises MediaError on anomalies."""
    streams = doc.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("no_video", "upload has no video stream")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = doc.get("format") or {}
    try:
        duration_s = float(video.get("duration") or fmt.get("duration") or 0.0)
        width = int(video["width"])
        height = int(video["height"])
    except (KeyError, TypeError, ValueError):
        raise MediaError("unparseable", "video stream lacks width/height/duration") from None
    if duration_s <= 0:
        raise MediaError("unparseable", "non-positive duration")
    channels = int(audio.get("channels", 0)) if audio else 0
    sample_rate = int(audio.get("sample_rate", 0)) if audio else 0
    return MediaInfo(
        duration_s=duration_s,
        width=width,
        height=height,
        fps=_parse_rate(str(video.get("r_frame_rate", "0/0")), "r_frame_rate"),
        avg_fps=_parse_rate(str(video.get("avg_frame_rate", "0/0")), "avg_frame_rate"),
        vcodec=str(video.get("codec_name", "unknown")),
        container=str(fmt.get("format_name", "unknown")),
        acodec=str(audio["codec_name"]) if audio else None,
        audio_channels=channels,
        sample_rate=sample_rate,
        rotation_deg=_rotation_of(video),
        color_transfer=str(video.get("color_transfer", "unknown")),
        size_bytes=size_bytes,
    )


def probe_file(path: str, timeout_s: int = PROBE_TIMEOUT_S) -> MediaInfo:
    """Run ffprobe (arg list, no shell) and parse. Raises MediaError."""
    import os

    try:
        size_bytes = os.path.getsize(path)
    except OSError as exc:
        raise MediaError("unreadable", f"cannot stat {path!r}: {exc}") from None
    try:
        proc = subprocess.run(  # noqa: S603 — arg list with resolved binary, never shell
            [
                ffprobe_path(),
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                path,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise MediaError("probe_failed", f"ffprobe failed on {path!r}: {exc}") from None
    if proc.returncode != 0:
        raise MediaError("probe_failed", f"ffprobe exit {proc.returncode}: {proc.stderr[-500:]}")
    try:
        doc: dict[str, Any] = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError("unparseable", f"ffprobe emitted invalid JSON: {exc}") from None
    return parse_probe_json(doc, size_bytes)


def probe_duration_s(path: str, timeout_s: int = PROBE_TIMEOUT_S) -> float:
    """Container duration for any media (incl. audio-only). Raises MediaError."""
    try:
        proc = subprocess.run(  # noqa: S603 — arg list with resolved binary, never shell
            [
                ffprobe_path(),
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                path,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise MediaError("probe_failed", f"ffprobe failed on {path!r}: {exc}") from None
    if proc.returncode != 0:
        raise MediaError("probe_failed", f"ffprobe exit {proc.returncode}")
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise MediaError("unparseable", f"no duration for {path!r}: {exc}") from None
