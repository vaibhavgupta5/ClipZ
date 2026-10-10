"""EDL compiler tests: clips, speed, reframe, zoom, overlays, encoder pick."""

import pytest

from app.render.edl_to_ffmpeg import (
    FINAL,
    PREVIEW,
    build_inputs,
    build_video_filter,
    select_video_encoder,
)
from app.render.errors import RenderError
from app.timeline.schema import (
    CaptionConfig,
    OutputSpec,
    Overlay,
    Timeline,
    VideoClip,
    Zoom,
)
from app.timeline.transcript import Transcript, Word


def _timeline(**overrides):  # type: ignore[no-untyped-def]
    words = [
        Word(id="w1", start=0.0, end=0.4, text="a", display_text="a", lang="en"),
        Word(id="w2", start=0.5, end=0.9, text="b", display_text="b", lang="en"),
    ]
    base = dict(
        project_id="00000000-0000-0000-0000-000000000000",
        transcript=Transcript(words=words),
        video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=2.0)],
        captions=CaptionConfig(),
        output=OutputSpec(),
    )
    base.update(overrides)
    return Timeline(**base)


def test_clip_trim_and_speed_factor() -> None:
    tl = _timeline(video_clips=[VideoClip(id="c1", asset="a1", src_in=1.0, src_out=3.0, speed=2.0)])
    script, _ = build_video_filter(tl, PREVIEW)
    assert "trim=start=1.000:end=3.000" in script
    assert "setpts=0.5*PTS" in script


def test_reframe_dims_per_profile() -> None:
    script, _ = build_video_filter(_timeline(), PREVIEW)
    assert "scale=540:960:" in script
    script, _ = build_video_filter(_timeline(), FINAL)
    assert "scale=1080:1920:" in script


def test_reframe_split_bottom() -> None:
    tl = _timeline(reframe="split_bottom")
    script, _ = build_video_filter(tl, FINAL)
    assert "scale=1080:960:" in script
    assert "overlay=0:960" in script


def test_overlay_split_top() -> None:
    ov = Overlay(
        id="ins1",
        type="image",
        asset="img1",
        anchor_word="w1",
        offset_ms=0,
        duration_ms=1000,
        layout="split_top",
        license="CC0-1.0",
    )
    tl = _timeline(reframe="split_bottom", overlays=[ov])
    script, _ = build_video_filter(tl, FINAL, asset_labels={"img1": "[1:v]"})
    assert "scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960" in script
    # split panels appear as their word is SAID (word start), same as the segment splitter
    assert "overlay=0:0:enable='between(t,0.000,1.000)'" in script


def test_zoom_anchors_to_word_time() -> None:
    tl = _timeline(zooms=[Zoom(id="z1", anchor_word="w2", scale=1.15, dur_ms=220)])
    script, _ = build_video_filter(tl, PREVIEW)
    assert "t-0.500" in script  # w2 starts at output 0.5
    assert "crop=" in script


def test_overlay_enable_window_and_layout() -> None:
    ov = Overlay(
        id="ins1",
        type="image",
        asset="g1",
        anchor_word="w2",
        offset_ms=80,
        duration_ms=1100,
        layout="pip_pop",
        license="CC0-1.0",
    )
    tl = _timeline(overlays=[ov])
    script, _ = build_video_filter(tl, PREVIEW, asset_labels={"g1": "[1:v]"})
    assert "enable='between(t,0.980,2.080)'" in script
    assert "overlay=" in script


def test_overlay_top_banner_starts_at_word_start() -> None:
    ov = Overlay(
        id="hl1",
        type="headline_card",
        asset="hl_123",
        anchor_word="w1",
        offset_ms=0,
        duration_ms=1500,
        layout="top_banner",
        license="proprietary",
    )
    tl = _timeline(overlays=[ov])
    script, _ = build_video_filter(tl, PREVIEW, asset_labels={"hl_123": "[1:v]"})
    assert "enable='between(t,0.000,1.500)'" in script
    assert "overlay=(W-w)/2:67" in script or "overlay=(W-w)/2:" in script


def test_missing_overlay_asset_raises() -> None:
    ov = Overlay(
        id="ins1",
        type="image",
        asset="nope",
        anchor_word="w1",
        duration_ms=500,
        layout="pip_pop",
        license="CC0-1.0",
    )
    with pytest.raises(RenderError, match="nope"):
        build_video_filter(_timeline(overlays=[ov]), PREVIEW, asset_labels={})


def test_build_inputs_loop_for_images() -> None:
    args = build_inputs("/m.mp4", {"/g.png": 1.1})
    assert args[:2] == ["-i", "/m.mp4"]
    assert args[2:7] == ["-loop", "1", "-t", "1.100", "-i"]
    assert args[7] == "/g.png"


def test_encoder_selection_prefers_nvenc(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    import app.render.edl_to_ffmpeg as compiler

    monkeypatch.setattr(compiler, "_nvenc_cached", None)

    class _Proc:
        returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    assert select_video_encoder() == "h264_nvenc"

    class _Fail:
        returncode = 1

    monkeypatch.setattr(compiler, "_nvenc_cached", None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Fail())
    assert select_video_encoder() == "libx264"


def test_banners_are_drawn_over_inserts() -> None:
    banner = Overlay(
        id="hl1", type="headline_card", asset="hl", anchor_word="w1", offset_ms=0,
        duration_ms=1500, layout="top_banner", license="own",
    )  # fmt: skip
    panel = Overlay(
        id="ins2", type="image", asset="img", anchor_word="w1", offset_ms=0,
        duration_ms=1000, layout="split_top", license="CC0-1.0",
    )  # fmt: skip
    tl = _timeline(reframe="split_bottom", overlays=[banner, panel])
    script, _ = build_video_filter(tl, FINAL, asset_labels={"hl": "[1:v]", "img": "[2:v]"})
    assert script.index("[2:v]") < script.index("[1:v]")  # banner composited last
