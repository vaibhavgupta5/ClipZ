"""ffmpeg runner: arg lists only, never shell. stderr tail on failure."""

import shutil
import subprocess

from app.media.errors import MediaError

FFMPEG_TIMEOUT_S = 600


def ffmpeg_path() -> str:
    """Resolve the ffmpeg binary or raise."""
    path = shutil.which("ffmpeg")
    if path is None:
        raise MediaError("no_ffmpeg", "ffmpeg binary not found on PATH")
    return path


def run_ffmpeg(args: list[str], timeout_s: int = FFMPEG_TIMEOUT_S) -> None:
    """Run an ffmpeg arg list (first element must be the binary). Raises MediaError."""
    if not args:
        raise MediaError("bad_args", "empty ffmpeg arg list")
    try:
        proc = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise MediaError("ffmpeg_failed", f"ffmpeg did not complete: {exc}") from None
    if proc.returncode != 0:
        raise MediaError("ffmpeg_failed", f"ffmpeg exit {proc.returncode}: {proc.stderr[-2000:]}")


def run_capture(args: list[str], timeout_s: int = FFMPEG_TIMEOUT_S) -> bytes:
    """Run an arg list and return stdout bytes. Raises MediaError."""
    if not args:
        raise MediaError("bad_args", "empty ffmpeg arg list")
    try:
        proc = subprocess.run(
            args, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout_s, check=False
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise MediaError("ffmpeg_failed", f"ffmpeg did not complete: {exc}") from None
    if proc.returncode != 0:
        raise MediaError("ffmpeg_failed", f"ffmpeg exit {proc.returncode}")
    return proc.stdout


def run_get_stderr(args: list[str], timeout_s: int = FFMPEG_TIMEOUT_S) -> str:
    """Run an arg list, expect success, return stderr text. Raises MediaError."""
    if not args:
        raise MediaError("bad_args", "empty ffmpeg arg list")
    try:
        proc = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise MediaError("ffmpeg_failed", f"ffmpeg did not complete: {exc}") from None
    if proc.returncode != 0:
        raise MediaError("ffmpeg_failed", f"ffmpeg exit {proc.returncode}: {proc.stderr[-2000:]}")
    return proc.stderr
