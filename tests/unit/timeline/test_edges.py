"""Edge tests pushing app/timeline coverage to the 95% gate."""

import pytest
from pydantic import ValidationError

from app.timeline import ops, snap
from app.timeline.errors import OpError
from app.timeline.schema import (
    CaptionConfig,
    MusicBed,
    Overlay,
    SfxHit,
    Timeline,
    VideoClip,
    Zoom,
)
from app.timeline.transcript import Transcript, Word
from tests.unit.timeline.test_ops import _timeline


def test_reversed_ranges_rejected() -> None:
    with pytest.raises(OpError, match="reversed_range"):
        ops.delete_range(_timeline(), "w4", "w2")
    with pytest.raises(OpError, match="reversed_range"):
        ops.trim(_timeline(), "w4", "w2")


def test_delete_range_with_pad_expands_to_word_edges() -> None:
    # pad 400ms from w3 [1.0,1.4] reaches into w2 (..0.9) and w4 (1.5..) -> all included
    tl, _ = ops.delete_range(_timeline(), "w3", "w3", pad_ms=400)
    bounds = [(c.src_in, c.src_out) for c in tl.video_clips]
    assert bounds == [(0.0, 0.5), (1.9, 3.0)]


def test_split_at_existing_boundary_is_noop() -> None:
    tl, diff = ops.split(_timeline(), "w1")  # w1.start == clip start
    assert diff.details.get("noop")
    assert len(tl.video_clips) == 1


def test_duplicate_and_unknown_ids_rejected() -> None:
    ov = Overlay(
        id="ins1",
        type="image",
        asset="g",
        anchor_word="w1",
        duration_ms=500,
        layout="pip_pop",
        license="CC0-1.0",
    )
    tl, _ = ops.add_overlay(_timeline(), ov)
    with pytest.raises(OpError, match="duplicate_id"):
        ops.add_overlay(tl, ov)
    with pytest.raises(OpError, match="unknown_overlay"):
        ops.remove_overlay(tl, "nope")

    z = Zoom(id="z1", anchor_word="w1", scale=1.1, dur_ms=200)
    tl, _ = ops.add_zoom(tl, z)
    with pytest.raises(OpError, match="duplicate_id"):
        ops.add_zoom(tl, z)
    with pytest.raises(OpError, match="unknown_zoom"):
        ops.remove_zoom(tl, "nope")

    hit = SfxHit(id="s1", asset="pop", anchor_word="w1", license="CC0-1.0")
    tl, _ = ops.add_sfx(tl, hit)
    with pytest.raises(OpError, match="duplicate_id"):
        ops.add_sfx(tl, hit)
    with pytest.raises(OpError, match="unknown_sfx"):
        ops.remove_sfx(tl, "nope")
    tl, _ = ops.remove_sfx(tl, "s1")
    assert tl.sfx == []


def test_set_captions_and_music() -> None:
    tl, diff = ops.set_captions(_timeline(), CaptionConfig(mode="off"))
    assert tl.captions.mode == "off"
    assert diff.op == "set_captions"
    tl, _ = ops.set_music(tl, MusicBed(asset="m1", license="CC-BY-4.0"))
    assert tl.music is not None and tl.music.asset == "m1"
    tl, diff = ops.set_music(tl, None)
    assert tl.music is None
    assert diff.details["asset"] == "none"


def test_duplicate_clip_and_overlay_ids_rejected_by_schema() -> None:
    base = _timeline()
    with pytest.raises(ValidationError, match="duplicate clip ids"):
        Timeline(
            project_id=base.project_id,
            transcript=base.transcript,
            video_clips=[
                VideoClip(id="c1", asset="a1", src_in=0.0, src_out=1.0),
                VideoClip(id="c1", asset="a1", src_in=1.0, src_out=2.0),
            ],
        )
    ov = Overlay(
        id="x",
        type="image",
        asset="g",
        anchor_word="w1",
        duration_ms=500,
        layout="pip_pop",
        license="CC0-1.0",
    )
    with pytest.raises(ValidationError, match="duplicate overlay ids"):
        Timeline(
            project_id=base.project_id,
            transcript=base.transcript,
            video_clips=base.video_clips,
            overlays=[ov, ov],
        )


def test_duplicate_word_ids_rejected() -> None:
    w = Word(id="w1", start=0.0, end=0.4, text="x", display_text="x", lang="en")
    with pytest.raises(ValidationError, match="duplicate word ids"):
        Transcript(words=[w, w])


def test_snap_unknown_and_cut_words() -> None:
    with pytest.raises(OpError, match="unknown_word"):
        snap.output_time_of(_timeline(), "w99")
    tl, _ = ops.delete_words(_timeline(), ["w1"])
    with pytest.raises(OpError, match="cut_word"):
        snap.output_time_of(tl, "w1")


def test_output_range_clamps_past_timeline_edges() -> None:
    tl = _timeline()
    lo, hi = snap.output_range_of(tl, "w5", "w6", pad_ms=5000)  # huge pad
    assert (lo, hi) == pytest.approx((0.0, tl.duration_s))
    with pytest.raises(OpError, match="unknown_word"):
        snap.output_range_of(tl, "w1", "w99")


def test_snap_boundary_moves_outward_only() -> None:
    tl = _timeline()
    assert snap.snap_boundary(tl, 0.6, "backward") == pytest.approx(0.5)  # inside w2
    assert snap.snap_boundary(tl, 0.6, "forward") == pytest.approx(0.9)
    assert snap.snap_boundary(tl, 0.45, "forward") == pytest.approx(0.45)  # in gap: untouched


def test_split_id_collision_gets_suffix() -> None:
    tl, _ = ops.split(_timeline(), "w3")  # c1 -> c1, c1b
    tl, _ = ops.split(tl, "w2")  # splits c1 [0,1.0] at 0.5; "c1b" taken -> suffix
    ids = [c.id for c in tl.video_clips]
    assert ids == ["c1", "c1b2", "c1b"]
    assert len(set(ids)) == 3


def test_cut_clips_skips_float_dust_pieces() -> None:
    clips = ops._cut_clips_at(
        [VideoClip(id="c1", asset="a1", src_in=0.0, src_out=3.0)], [1.0, 1.0 + 5e-7]
    )
    assert [(c.src_in, c.src_out) for c in clips] == [(0.0, 1.0), (1.0 + 5e-7, 3.0)]


def test_split_double_collision_increments_suffix_twice() -> None:
    base = _timeline()
    tl = Timeline(
        project_id=base.project_id,
        transcript=base.transcript,
        video_clips=[
            VideoClip(id="c1", asset="a1", src_in=0.0, src_out=3.0),
            VideoClip(id="c1b", asset="a1", src_in=4.0, src_out=5.0),
            VideoClip(id="c1b2", asset="a1", src_in=6.0, src_out=7.0),
        ],
    )
    tl, _ = ops.split(tl, "w3")
    assert [c.id for c in tl.video_clips] == ["c1", "c1b3", "c1b", "c1b2"]


def test_delete_noncontiguous_words_makes_two_runs() -> None:
    tl, diff = ops.delete_words(_timeline(), ["w1", "w3"])
    assert diff.op == "delete_words"
    assert tl.duration_s == pytest.approx(3.0 - 0.4 - 0.4)


def test_output_range_clamps_to_nothing_when_span_fully_cut() -> None:
    tl, _ = ops.trim(_timeline(), "w1", "w1")  # surviving media [0, 0.4]
    lo, hi = snap.output_range_of(tl, "w5", "w6")  # transcript still has all words
    assert (lo, hi) == pytest.approx((tl.duration_s, tl.duration_s))
    tl2, _ = ops.delete_words(_timeline(), ["w1", "w2", "w3", "w4", "w5"])
    lo, hi = snap.output_range_of(tl2, "w1", "w1", pad_ms=0)
    assert (lo, hi) == pytest.approx((0.0, 0.0))
