"""Ops tests: pure functions, word-ID anchors, snapped cuts, immutable inputs."""

import pytest

from app.timeline import ops
from app.timeline.errors import OpError
from app.timeline.schema import CaptionConfig, OutputSpec, Overlay, Timeline, VideoClip, Zoom
from app.timeline.transcript import Transcript, Word


def _transcript() -> Transcript:
    # 6 words over 0..3s in one asset
    return Transcript(
        words=[
            Word(
                id=f"w{i + 1}", start=i * 0.5, end=i * 0.5 + 0.4, text=t, display_text=t, lang="en"
            )
            for i, t in enumerate(["a", "b", "c", "d", "e", "f"])
        ]
    )


def _timeline() -> Timeline:
    return Timeline(
        project_id="00000000-0000-0000-0000-000000000000",
        transcript=_transcript(),
        video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=3.0)],
        captions=CaptionConfig(),
        output=OutputSpec(),
    )


def test_delete_words_removes_middle_and_keeps_rest() -> None:
    tl, diff = ops.delete_words(_timeline(), ["w2", "w3"])
    assert diff.op == "delete_words"
    assert tl.duration_s == pytest.approx(2.1)  # hole w2.start..w3.end = 0.9s
    assert len(tl.video_clips) == 2  # split around the hole


def test_delete_words_never_cuts_inside_word() -> None:
    tl, _ = ops.delete_words(_timeline(), ["w2"])
    bounds = [(c.src_in, c.src_out) for c in tl.video_clips]
    # w2 spans 0.5..0.9 -> remaining clips must end <= 0.5 and start >= 0.9
    assert bounds == [(0.0, 0.5), (0.9, 3.0)]


def test_ops_do_not_mutate_input() -> None:
    tl = _timeline()
    before = tl.model_dump()
    ops.delete_words(tl, ["w2"])
    ops.trim(tl, "w2", "w4")
    assert tl.model_dump() == before


def test_trim_keeps_word_range_with_padding() -> None:
    tl, _ = ops.trim(_timeline(), "w2", "w3", pad_ms=100)
    assert len(tl.video_clips) == 1
    c = tl.video_clips[0]
    assert c.src_in == pytest.approx(0.4)  # w2.start - pad
    assert c.src_out == pytest.approx(1.5)  # w3.end + pad


def test_split_creates_two_clips_at_word_start() -> None:
    tl, _ = ops.split(_timeline(), "w3")
    assert [c.id for c in tl.video_clips] == ["c1", "c1b"]
    assert tl.video_clips[0].src_out == pytest.approx(1.0)
    assert tl.video_clips[1].src_in == pytest.approx(1.0)


def test_delete_range_unknown_word_errors_for_llm() -> None:
    with pytest.raises(OpError) as exc:
        ops.delete_range(_timeline(), "w2", "w99")
    assert "w99" in str(exc.value)


def test_reorder_permutes_clips() -> None:
    tl, _ = ops.split(_timeline(), "w3")
    tl2, diff = ops.reorder(tl, ["c1b", "c1"])
    assert [c.id for c in tl2.video_clips] == ["c1b", "c1"]
    assert diff.op == "reorder"


def test_reorder_with_missing_id_rejected() -> None:
    with pytest.raises(OpError, match="c9"):
        ops.reorder(_timeline(), ["c9"])


def test_set_speed_on_word_range() -> None:
    tl, _ = ops.set_speed(_timeline(), "w1", "w2", 2.0)
    assert tl.duration_s == pytest.approx(2.55)  # 0.9s at 2x (=0.45) + 2.1s at 1x
    assert all(c.speed == 2.0 for c in tl.video_clips if c.src_out <= 1.0)


def test_set_speed_out_of_bounds_rejected() -> None:
    with pytest.raises(OpError, match="0.25.*4"):
        ops.set_speed(_timeline(), "w1", "w2", 10.0)


def test_add_overlay_with_dangling_anchor_rejected() -> None:
    ov = Overlay(
        id="ins1",
        type="image",
        asset="g1",
        anchor_word="w99",
        duration_ms=1000,
        layout="pip_pop",
        license="CC0-1.0",
    )
    with pytest.raises(OpError, match="w99"):
        ops.add_overlay(_timeline(), ov)


def test_add_remove_overlay_and_zoom_round_trip() -> None:
    ov = Overlay(
        id="ins1",
        type="image",
        asset="g1",
        anchor_word="w2",
        duration_ms=1000,
        layout="pip_pop",
        license="CC0-1.0",
    )
    tl, _ = ops.add_overlay(_timeline(), ov)
    assert len(tl.overlays) == 1
    tl, _ = ops.remove_overlay(tl, "ins1")
    assert tl.overlays == []

    tl, _ = ops.add_zoom(tl, Zoom(id="z1", anchor_word="w2", scale=1.15, dur_ms=220))
    assert len(tl.zooms) == 1
    tl, _ = ops.remove_zoom(tl, "z1")
    assert tl.zooms == []


def test_deleting_everything_is_rejected() -> None:
    tl = _timeline().model_copy(
        update={"video_clips": [VideoClip(id="c1", asset="a1", src_in=0.0, src_out=2.9)]},
        deep=True,
    )
    with pytest.raises(OpError, match="empty"):
        ops.delete_words(tl, ["w1", "w2", "w3", "w4", "w5", "w6"])
