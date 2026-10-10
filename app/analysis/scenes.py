"""Scene cuts via ffmpeg's scene filter + showinfo parse (no cv2 needed)."""

import re
import subprocess

from app.analysis.models import SceneCut
from app.media.ffmpeg import ffmpeg_path

_SHOWINFO_RE = re.compile(r"pts_time:([0-9.]+)")


class SceneDetector:
    """Shot boundaries from frame-difference scores. Version: ffmpeg-scene-1."""

    VERSION = "ffmpeg-scene-1"

    def __init__(self, threshold: float = 0.4) -> None:
        self.threshold = threshold

    def cuts(self, video_path: str) -> list[SceneCut]:
        """Cut times in source seconds, deterministic for a fixed build/threshold."""
        proc = subprocess.run(  # noqa: S603 — fixed filter template, path is a local file
            [
                ffmpeg_path(),
                "-hide_banner",
                "-nostats",
                "-i",
                video_path,
                "-filter:v",
                f"select='gt(scene,{self.threshold})',showinfo",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            from app.media.errors import MediaError

            raise MediaError("scene_failed", f"scene detection exit {proc.returncode}")
        seen: set[float] = set()
        for line in proc.stderr.splitlines():
            if "showinfo" not in line:
                continue
            match = _SHOWINFO_RE.search(line)
            if match:
                seen.add(round(float(match.group(1)), 3))
        return [SceneCut(at=t) for t in sorted(seen)]
