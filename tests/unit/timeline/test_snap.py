"""Snapping tests: word IDs -> output seconds through cuts and speed changes."""

import pytest

from app.timeline import snap
from app.timeline.errors import OpError
from app.timeline.schema import CaptionConfig, OutputSpec, Timeline, VideoClip
from app.timeline.transcript import Transcript, Word


def _timeline() -> Timeline:
    # words 0..3s; clips: [0,1] then jump to [2,3]
    words = [
        Word(id=f"w{i + 1}", start=i * 0.5, end=i * 0.5 + 0.4, text=t, display_text=t, lang="en")
        for i, t in enumerate(["a", "b", "c", "d", "e", "f"])
    ]
    return Timeline(
        project_id="00000000-0000-0000-0000-000000000000",
        transcript=Transcript(words=words),
        video_clips=[
            VideoClip(id="c1", asset="a1", src_in=0.0, src_out=1.0),
            VideoClip(id="c2", asset="a1", src_in=2.0, src_out=3.0),
        ],
        captions=CaptionConfig(),
        output=OutputSpec(),
    )


def test_output_time_follows_cuts() -> None:
    tl = _timeline()
    assert snap.output_time_of(tl, "w1") == pytest.approx(0.0)  # src 0.0 in c1
    assert snap.output_time_of(tl, "w2") == pytest.approx(0.5)  # src 0.5 in c1
    assert snap.output_time_of(tl, "w5") == pytest.approx(1.0)  # src 2.0 in c2 -> out 1.0
    assert snap.output_time_of(tl, "w6") == pytest.approx(1.5)


def test_cut_word_has_no_output_time() -> None:
    tl = _timeline()
    with pytest.raises(OpError, match="w3"):
        snap.output_time_of(tl, "w3")  # src 1.0..1.4 removed


def test_speed_divides_output_time() -> None:
    tl = _timeline().model_copy(
        update={
            "video_clips": [VideoClip(id="c1", asset="a1", src_in=0.0, src_out=2.0, speed=2.0)]
        },
        deep=True,
    )
    assert snap.output_time_of(tl, "w2") == pytest.approx(0.25)  # 0.5 src / 2


def test_output_range_expands_with_padding() -> None:
    tl = _timeline()
    # w5..w6 padded = src [1.9, 3.0]; 1.9 is cut away -> clamps to c2 start (out 1.0)
    lo, hi = snap.output_range_of(tl, "w5", "w6", pad_ms=100)
    assert (lo, hi) == pytest.approx((1.0, 2.0))
