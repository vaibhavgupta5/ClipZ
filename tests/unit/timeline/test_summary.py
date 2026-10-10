"""Summary tests: compact deterministic LLM context; inline snapshot is the contract."""

from app.timeline.schema import (
    CaptionConfig,
    MusicBed,
    OutputSpec,
    Overlay,
    Timeline,
    VideoClip,
    Zoom,
)
from app.timeline.summary import summarize
from app.timeline.transcript import Transcript, Word


def _timeline() -> Timeline:
    words = [
        Word(id="w1", start=0.0, end=0.4, text="a", display_text="a", lang="en"),
        Word(id="w2", start=0.5, end=0.9, text="b", display_text="b", lang="en"),
    ]
    return Timeline(
        project_id="00000000-0000-0000-0000-000000000000",
        version=3,
        transcript=Transcript(words=words),
        video_clips=[
            VideoClip(id="c1", asset="a1", src_in=0.0, src_out=1.0),
            VideoClip(id="c2", asset="a1", src_in=5.0, src_out=9.0),
        ],
        captions=CaptionConfig(mode="word_highlight", position="center_lower", words_per_line=2),
        zooms=[Zoom(id="z1", anchor_word="w2", scale=1.15, dur_ms=220)],
        overlays=[
            Overlay(
                id="ins1",
                type="image",
                asset="g1",
                anchor_word="w2",
                offset_ms=80,
                duration_ms=1100,
                layout="pip_pop",
                license="CC0-1.0",
            )
        ],
        music=MusicBed(asset="m1", license="CC-BY-4.0", attribution_required=True),
        output=OutputSpec(),
        style_id="tech_explainer_fast_v1",
    )


EXPECTED = """timeline v3 | 5.000s | 2 clips | 12.0 cuts/min | words w1..w2 | 0 beats
captions word_highlight center_lower wpl=2 size=84 #FFFFFF/#FFD400
zooms 1 | overlays 1 | music yes | sfx 0
style tech_explainer_fast_v1 | reframe center | audio loudnorm-default
overlays: ins1(image/pip_pop @w2+80ms 1100ms)
music: m1 [attribution required]"""


def test_summary_snapshot() -> None:
    assert summarize(_timeline()) == EXPECTED


def test_summary_without_optionals() -> None:
    tl = _timeline().model_copy(update={"music": None, "overlays": [], "style_id": None})
    text = summarize(tl)
    assert "music no" in text
    assert "style none" in text
    assert "overlays:" not in text
