"""Segment cache key: must change when the renderer's output for a slice can change."""

import pytest

from app.render import segments
from tests.unit.timeline.test_ops import _timeline


def test_slice_hash_changes_with_renderer_version(monkeypatch: pytest.MonkeyPatch) -> None:
    seg = segments.split_segments(_timeline())[0]
    before = segments.slice_hash(seg, "preview", "", {})
    assert segments.slice_hash(seg, "preview", "", {}) == before  # deterministic
    monkeypatch.setattr(segments, "RENDER_VERSION", "test-bump")
    assert segments.slice_hash(seg, "preview", "", {}) != before


def test_captions_are_hidden_under_memes() -> None:
    from app.timeline.schema import Overlay

    meme = Overlay(
        id="m1",
        type="meme",
        asset="memes:x:0123456789abcdef",
        anchor_word="w2",
        offset_ms=0,
        duration_ms=800,
        layout="blur_bg_contain",
        license="unknown",
        commercial_use=False,
    )
    tl = _timeline().model_copy(update={"overlays": [meme]})
    cues = [c.id for seg in segments.split_segments(tl) for c in seg.cues]
    # w2 ends at 0.9 s; the meme covers 0.9-1.7 s, hiding w3 (1.0-1.4) and w4 (1.5-1.9)
    assert cues == ["w1", "w2", "w5", "w6"]
