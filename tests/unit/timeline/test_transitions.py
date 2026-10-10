"""Smart transitions: styled only at real cuts, spaced, never under full-screen inserts."""

from app.timeline.schema import Overlay, Timeline, VideoClip
from app.timeline.snap import transition_points
from tests.unit.inserts.test_keywords import _tl


def _cut(spans: list[tuple[float, float]], style: str = "whip") -> Timeline:
    tl = _tl(" ".join(f"word{i}" for i in range(40)))
    clips = [
        VideoClip(id=f"c{i}", asset="a1", src_in=a, src_out=b) for i, (a, b) in enumerate(spans)
    ]
    return tl.model_copy(update={"video_clips": clips, "transition": style})


def test_breath_trims_stay_hard_cuts() -> None:
    # 0.2 s gaps are silence trims; the 2 s jump is a real cut
    tl = _cut([(0, 4), (4.2, 8), (10, 14), (14.2, 18)])
    assert transition_points(tl) == {2}


def test_transitions_are_spaced() -> None:
    tl = _cut([(0, 4), (5, 6), (7, 8), (9, 14), (15, 19)])
    # cuts land at 4, 5, 6, 11 s output; >= 3 s apart keeps 4 and 11
    assert transition_points(tl) == {1, 4}


def test_none_style_and_covered_cuts() -> None:
    assert transition_points(_cut([(0, 4), (6, 10)], style="none")) == set()
    tl = _cut([(0, 4), (6, 10)])
    cover = Overlay(
        id="ins1", type="image", asset="images:x", anchor_word="w7", offset_ms=0,
        duration_ms=1500, layout="blur_bg_contain", license="CC0-1.0",
    )  # fmt: skip
    # w7 ends at 3.4 s; the insert covers 3.4-4.9 s, hiding the cut at 4 s
    assert transition_points(tl.model_copy(update={"overlays": [cover]})) == set()
