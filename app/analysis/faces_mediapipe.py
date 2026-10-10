"""MediaPipe face tracking adapter (lazy). Default pipeline uses FakeFaces + reframe-center."""

from app.analysis.adapters import ModelUnavailable
from app.analysis.models import FaceBox

SAMPLE_FPS = 5


class MediaPipeFaces:
    """Per-frame face boxes at 5 fps, normalized coords. Version: mp-face-1."""

    VERSION = "mp-face-1"

    def __init__(self) -> None:
        try:
            import mediapipe as mp  # noqa: F401
        except ImportError:
            raise ModelUnavailable("mediapipe", "mediapipe") from None
        self._mp = mp

    def track(self, video_path: str) -> list[FaceBox]:
        """Track faces (requires mediapipe + opencv; GPU track)."""
        raise ModelUnavailable(
            "face-tracking-runtime",
            "mediapipe + opencv-python (wired in the GPU worker image, Phase 9)",
        )
