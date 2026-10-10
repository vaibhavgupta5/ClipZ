"""Reframe + audio_fx tests."""

import pytest

from app.timeline import ops
from app.timeline.schema import AudioEffect, Timeline
from tests.unit.timeline.test_ops import _timeline


def test_reframe_defaults_center_and_validates() -> None:
    from pydantic import ValidationError

    assert _timeline().reframe == "center"
    base = _timeline()
    with pytest.raises(ValidationError):
        Timeline(
            project_id=base.project_id,
            transcript=base.transcript,
            video_clips=base.video_clips,
            reframe="diagonal",  # type: ignore[arg-type]
        )


def test_set_reframe_and_reject_bad_mode() -> None:
    from app.timeline.errors import OpError

    tl, diff = ops.set_reframe(_timeline(), "blur")
    assert tl.reframe == "blur" and diff.op == "set_reframe"
    with pytest.raises(OpError, match="bad_reframe"):
        ops.set_reframe(_timeline(), "diagonal")


def test_set_audio_fx_replaces_effects() -> None:
    tl, diff = ops.set_audio_fx(
        _timeline(),
        [AudioEffect(type="denoise"), AudioEffect(type="loudnorm", params={"lufs": -14.0})],
    )
    assert diff.op == "set_audio_fx"
    assert [e.type for e in tl.audio_fx] == ["denoise", "loudnorm"]
    assert _timeline().audio_fx == []  # input untouched


def test_blur_reframe_compiles() -> None:
    from app.render.edl_to_ffmpeg import PREVIEW, build_video_filter

    script, _ = build_video_filter(_timeline(), PREVIEW, reframe="blur")
    assert "boxblur" in script
