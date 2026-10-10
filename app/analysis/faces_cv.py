"""CPU face tracker: OpenCV's YuNet detector on frames sampled by FFmpeg (ADR-022).

YuNet (MIT, Shiqi Yu; model vendored in data/yunet with its licence) is a ~230 KB CNN that
runs in real time on a CPU, so split view and face framing get real tracks without the GPU
worker. MediaPipe (Phase 9) may replace it. Results are cached by the video's content hash.
"""

import json
from pathlib import Path

from app.analysis.adapters import ModelUnavailable
from app.analysis.models import FaceBox
from app.media.ffmpeg import ffmpeg_path, run_capture
from app.media.probe import probe_file

MODEL = Path(__file__).parent / "data" / "yunet" / "face_detection_yunet_2023mar.onnx"
SAMPLE_FPS = 2.0
SAMPLE_WIDTH = 320  # detection resolution: faces in a reel are large
MIN_SCORE = 0.7
VERSION = "yunet-1"  # bump when detection output changes (invalidates the face cache)


class YuNetFaces:
    """Largest confident face per sampled frame, normalized top-left boxes. Version: yunet-1."""

    VERSION = VERSION

    def __init__(self) -> None:
        try:
            import cv2
        except ImportError:
            raise ModelUnavailable("opencv", "opencv-python-headless<5") from None
        if not MODEL.is_file():
            raise ModelUnavailable("yunet", f"the model file {MODEL.name} (vendored)")
        self._cv2 = cv2

    def track(self, video_path: str) -> list[FaceBox]:
        """Sample frames at 2 fps (320 px wide) and detect the main face in each."""
        import numpy as np

        info = probe_file(video_path)
        w = SAMPLE_WIDTH
        h = max(2, round(info.height * w / info.width / 2) * 2)
        raw = run_capture(
            [
                ffmpeg_path(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                video_path,
                "-vf",
                f"fps={SAMPLE_FPS},scale={w}:{h}",
                "-pix_fmt",
                "bgr24",
                "-f",
                "rawvideo",
                "pipe:1",
            ]
        )
        frames = np.frombuffer(raw, dtype=np.uint8)
        size = w * h * 3
        detector = self._cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), MIN_SCORE)
        boxes: list[FaceBox] = []
        for i in range(frames.size // size):
            frame = frames[i * size : (i + 1) * size].reshape(h, w, 3)
            _, found = detector.detect(frame)
            if found is None or len(found) == 0:
                continue
            x, y, bw, bh = (float(v) for v in max(found, key=lambda r: r[2] * r[3])[:4])
            x0, y0 = max(0.0, x), max(0.0, y)
            boxes.append(
                FaceBox(
                    t=round(i / SAMPLE_FPS, 3),
                    x=min(1.0, x0 / w),
                    y=min(1.0, y0 / h),
                    w=min(1.0, (min(w, x + bw) - x0) / w),
                    h=min(1.0, (min(h, y + bh) - y0) / h),
                )
            )
        return boxes


def track_cached(video_path: str, content_sha: str, cache_dir: Path) -> list[FaceBox]:
    """Face boxes for a video, cached by content hash + tracker version (rule 5).

    Best effort: a missing dependency or a failed decode yields no faces (the renderer then
    uses its default framing); only successful runs are cached.
    """
    cache = cache_dir / f"{content_sha}-{VERSION}.json"
    if cache.is_file():
        return [FaceBox.model_validate(b) for b in json.loads(cache.read_text())]
    try:
        boxes = YuNetFaces().track(video_path)
    except Exception:  # noqa: BLE001 - framing is best effort; a render must not fail on it
        return []
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([b.model_dump() for b in boxes]))
    return boxes
