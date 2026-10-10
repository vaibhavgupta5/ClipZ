"""Validator tests: every policy check fires with an LLM-actionable error."""

import pytest

from app.timeline.errors import TimelineInvalid
from app.timeline.schema import (
    CaptionConfig,
    MusicBed,
    OutputSpec,
    Overlay,
    Timeline,
    VideoClip,
    Zoom,
)
from app.timeline.transcript import Transcript, Word
from app.timeline.validate import ensure_valid, validate_timeline


def _words(n: int, step: float = 0.5) -> list[Word]:
    return [
        Word(
            id=f"w{i + 1}",
            start=i * step,
            end=i * step + 0.4,
            text="x",
            display_text="x",
            lang="en",
        )
        for i in range(n)
    ]


def _timeline(**overrides):  # type: ignore[no-untyped-def]
    base = dict(
        project_id="00000000-0000-0000-0000-000000000000",
        transcript=Transcript(words=_words(4)),
        video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=2.0)],
        captions=CaptionConfig(),
        output=OutputSpec(),
    )
    base.update(overrides)
    return Timeline(**base)


def test_valid_timeline_passes() -> None:
    ensure_valid(_timeline())  # must not raise


def test_exactly_90s_allowed_but_90_001_rejected() -> None:
    ensure_valid(_timeline(video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=90.0)]))
    with pytest.raises(TimelineInvalid, match="too_long"):
        ensure_valid(
            _timeline(video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=90.001)])
        )


def test_overlapping_source_ranges_rejected() -> None:
    tl = _timeline(
        video_clips=[
            VideoClip(id="c1", asset="a1", src_in=0.0, src_out=1.0),
            VideoClip(id="c2", asset="a1", src_in=0.5, src_out=2.0),
        ]
    )
    with pytest.raises(TimelineInvalid, match="clip_overlap"):
        ensure_valid(tl)


def test_dangling_anchor_rejected() -> None:
    tl = _timeline(zooms=[Zoom(id="z1", anchor_word="w99", scale=1.1, dur_ms=220)])
    with pytest.raises(TimelineInvalid, match="dangling_anchor"):
        ensure_valid(tl)


def test_missing_overlay_license_rejected() -> None:
    ov = Overlay(
        id="ins1", type="image", asset="g1", anchor_word="w1", duration_ms=1000, layout="pip_pop"
    )
    with pytest.raises(TimelineInvalid, match="missing_license"):
        ensure_valid(_timeline(overlays=[ov]))


def test_missing_music_license_rejected() -> None:
    with pytest.raises(TimelineInvalid, match="missing_license"):
        ensure_valid(_timeline(music=MusicBed(asset="m1")))


def test_too_many_words_per_line_rejected() -> None:
    tl = _timeline(captions=CaptionConfig(words_per_line=5))
    with pytest.raises(TimelineInvalid, match="bad_caption_layout"):
        ensure_valid(tl)


def test_bad_zoom_and_speed_rejected() -> None:
    tl = _timeline(zooms=[Zoom(id="z1", anchor_word="w1", scale=2.5, dur_ms=220)])
    with pytest.raises(TimelineInvalid, match="bad_zoom"):
        ensure_valid(tl)
    tl = _timeline(video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=1.0, speed=5.0)])
    with pytest.raises(TimelineInvalid, match="bad_speed"):
        ensure_valid(tl)


def test_unknown_asset_rejected_when_registry_given() -> None:
    with pytest.raises(TimelineInvalid, match="unknown_asset"):
        ensure_valid(_timeline(), known_asset_ids={"other"})
    ensure_valid(_timeline(), known_asset_ids={"a1"})  # must not raise


def test_errors_are_actionable() -> None:
    errors = validate_timeline(_timeline(music=MusicBed(asset="m1")))
    assert len(errors) == 1
    err = errors[0]
    assert err.code and err.field and err.message and err.hint
