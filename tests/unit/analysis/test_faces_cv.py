"""CPU face tracker: real model on a face-free clip, content-hash cache, best effort."""

import json
from pathlib import Path

import pytest

from app.analysis import faces_cv
from app.analysis.models import FaceBox
from app.media.ffmpeg import ffmpeg_path, run_ffmpeg


def test_no_faces_in_a_test_pattern(tmp_path: Path) -> None:
    clip = tmp_path / "pattern.mp4"
    run_ffmpeg(
        [ffmpeg_path(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=360x640:duration=2",
         "-pix_fmt", "yuv420p", str(clip)]
    )  # fmt: skip
    assert faces_cv.YuNetFaces().track(str(clip)) == []


def test_results_are_cached_by_content_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = FaceBox(t=0.5, x=0.4, y=0.2, w=0.2, h=0.2)
    calls: list[str] = []

    class Tracker:
        def track(self, path: str) -> list[FaceBox]:
            calls.append(path)
            return [box]

    monkeypatch.setattr(faces_cv, "YuNetFaces", Tracker)
    assert faces_cv.track_cached("v.mp4", "abc", tmp_path) == [box]
    assert faces_cv.track_cached("v.mp4", "abc", tmp_path) == [box]
    assert calls == ["v.mp4"]  # second call served from the cache
    cached = json.loads((tmp_path / "abc-yunet-1.json").read_text())
    assert cached[0]["x"] == 0.4


def test_a_failing_tracker_never_fails_the_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken:
        def track(self, path: str) -> list[FaceBox]:
            raise RuntimeError("decode failed")

    monkeypatch.setattr(faces_cv, "YuNetFaces", Broken)
    assert faces_cv.track_cached("v.mp4", "abc", tmp_path) == []
    assert not list(tmp_path.iterdir())  # failures are not cached
