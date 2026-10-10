"""Timeline schema tests: valid EDL builds, duration math, rejection of bad clips."""

import pytest
from pydantic import ValidationError

from app.timeline.schema import (
    CaptionConfig,
    MusicBed,
    OutputSpec,
    Timeline,
    VideoClip,
)
from app.timeline.transcript import Transcript, Word


def _transcript() -> Transcript:
    return Transcript(
        words=[
            Word(id="w1", start=0.0, end=0.4, text="a", display_text="a", lang="en"),
            Word(id="w2", start=0.5, end=0.9, text="b", display_text="b", lang="en"),
            Word(id="w3", start=1.0, end=1.4, text="c", display_text="c", lang="en"),
        ]
    )


def _timeline(**overrides):  # type: ignore[no-untyped-def]
    base = dict(
        project_id="00000000-0000-0000-0000-000000000000",
        transcript=_transcript(),
        video_clips=[
            VideoClip(id="c1", asset="a1", src_in=0.0, src_out=4.0),
            VideoClip(id="c2", asset="a1", src_in=9.0, src_out=15.0),
        ],
        captions=CaptionConfig(),
        output=OutputSpec(),
    )
    base.update(overrides)
    return Timeline(**base)


def test_valid_timeline_builds_with_duration() -> None:
    tl = _timeline()
    assert tl.duration_s == pytest.approx(10.0)
    assert tl.version == 1


def test_speed_halves_clip_duration() -> None:
    tl = _timeline(video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=4.0, speed=2.0)])
    assert tl.duration_s == pytest.approx(2.0)


def test_inverted_clip_rejected() -> None:
    with pytest.raises(ValidationError, match="src_out"):
        VideoClip(id="c1", asset="a1", src_in=4.0, src_out=4.0)


def test_nonpositive_speed_rejected() -> None:
    with pytest.raises(ValidationError, match="speed"):
        VideoClip(id="c1", asset="a1", src_in=0.0, src_out=4.0, speed=0.0)


def test_music_license_defaults_to_empty_for_validator() -> None:
    assert MusicBed(asset="m1").license == ""


def test_timeline_is_immutable() -> None:
    tl = _timeline()
    with pytest.raises(ValidationError):
        tl.version = 2  # type: ignore[misc]
