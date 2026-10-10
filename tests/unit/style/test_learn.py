"""Style learning: habits from the user's own past timelines -> a reviewable prompt."""

import uuid

from app.style.learn import StyleSample, learn_style, style_prompt
from app.timeline.schema import (
    AudioEffect,
    CaptionConfig,
    MusicBed,
    Overlay,
    SfxHit,
    Timeline,
    VideoClip,
    Zoom,
)
from app.timeline.transcript import Transcript, Word


def _tl(
    *,
    cut: bool = False,
    reframe: str = "center",
    captions: CaptionConfig | None = None,
    inserts: int = 0,
    kind: str = "image",
    headline: bool = False,
    transition: str = "none",
    zooms: int = 0,
    music: bool = False,
    sfx: bool = False,
    denoise: bool = False,
    text: str = "word",
) -> Timeline:
    """A 30 s timeline (60 words of 0.5 s) with the asked-for edits."""
    words = [
        Word(
            id=f"w{i + 1}",
            start=i * 0.5,
            end=i * 0.5 + 0.4,
            text=text,
            display_text=text,
            lang="en",
        )
        for i in range(60)
    ]
    clips = [VideoClip(id="c1", asset="master", src_in=0.0, src_out=30.0)]
    if cut:
        clips = [
            VideoClip(id="c1", asset="master", src_in=0.0, src_out=14.0),
            VideoClip(id="c2", asset="master", src_in=16.0, src_out=30.0),
        ]
    overlays = [
        Overlay(
            id=f"ins{n}",
            type=kind,  # type: ignore[arg-type]
            asset=f"{kind}s:a{n}",
            anchor_word=f"w{4 + 8 * n}",
            duration_ms=1200,
            layout="pip_pop",
            license="CC0-1.0",
        )
        for n in range(inserts)
    ]
    if headline:
        overlays.append(
            Overlay(
                id="hl",
                type="headline_card",
                asset="headline:x",
                anchor_word="w1",
                duration_ms=5000,
                layout="top_banner",
                license="own",
            )
        )
    return Timeline(
        project_id=uuid.uuid4(),
        transcript=Transcript(words=words),
        video_clips=clips,
        reframe=reframe,  # type: ignore[arg-type]
        captions=captions or CaptionConfig(),
        overlays=overlays,
        transition=transition,  # type: ignore[arg-type]
        zooms=[Zoom(id=f"z{n}", anchor_word=f"w{2 + 10 * n}", scale=1.1) for n in range(zooms)],
        music=MusicBed(asset="music:m", license="CC0-1.0", level_db=-22.0) if music else None,
        sfx=[SfxHit(id="auto1", asset="sfx:pop", anchor_word="w5", license="CC0-1.0")]
        if sfx
        else [],
        audio_fx=[AudioEffect(type="denoise")] if denoise else [],
    )


def _areas(samples: list[StyleSample]) -> dict[str, str]:
    profile = learn_style(samples, 30.0)
    return {h.area: h.instruction for h in profile.habits}


def test_nothing_to_learn_from_gives_no_prompt() -> None:
    profile = learn_style([], 30.0)
    assert profile.samples == 0 and profile.habits == []
    assert style_prompt(profile) is None


def test_habits_need_half_of_the_reels() -> None:
    samples = [
        StyleSample(_tl(music=True), music_genre="electronic"),
        StyleSample(_tl(music=True), music_genre="electronic"),
        StyleSample(_tl(zooms=2)),  # zooms in 1 of 3 reels: not a habit
    ]
    profile = learn_style(samples, 30.0)
    music = next(h for h in profile.habits if h.area == "music")
    assert music.share == 0.67 and "electronic background music" in music.instruction
    assert "-22 dB" in music.instruction
    assert not any("zoom" in h.instruction for h in profile.habits)


def test_exported_reels_count_double() -> None:
    samples = [
        StyleSample(_tl(reframe="adaptive"), exported=True),
        StyleSample(_tl(reframe="split_bottom")),
    ]
    assert "adaptive 9:16" in _areas(samples)["framing"]
    samples = [
        StyleSample(_tl(reframe="adaptive")),
        StyleSample(_tl(reframe="split"), exported=True),  # "split" is the same as split_bottom
    ]
    assert "split-screen 9:16" in _areas(samples)["framing"]


def test_center_crop_is_the_default_and_not_asked_for() -> None:
    assert "framing" not in _areas([StyleSample(_tl()), StyleSample(_tl(reframe="blur"))])


def test_counts_are_sized_for_the_new_video() -> None:
    samples = [StyleSample(_tl(inserts=3, zooms=2)), StyleSample(_tl(inserts=3, zooms=2))]
    # 3 visuals and 2 zooms per 30 s reel -> 6 and 4 for a 60 s video
    profile = learn_style(samples, 60.0)
    text = " ".join(h.instruction for h in profile.habits)
    assert "about 6 across the video" in text and "about 4 punch-in zooms" in text
    assert "as pop-up stickers" in text  # their usual layout outside split framing


def test_visual_kinds_follow_what_the_user_used() -> None:
    samples = [StyleSample(_tl(inserts=4, kind="icon")), StyleSample(_tl(inserts=2, kind="video"))]
    visuals = _areas(samples)["visuals"]
    assert "brand/tech logos, B-roll clips" in visuals


def test_caption_look_takes_each_field_majority() -> None:
    viral = CaptionConfig(
        position="center", size_px=96, uppercase=True, animation="pop", highlight_color="#00f0ff"
    )
    samples = [
        StyleSample(_tl(captions=viral)),
        StyleSample(_tl(captions=viral.model_copy(update={"size_px": 110}))),
        StyleSample(_tl()),  # default look
    ]
    captions = _areas(samples)["captions"]
    assert "uppercase, pop animation, 96 px" in captions
    assert "#00F0FF highlight" in captions and "in the middle" in captions
    off = [StyleSample(_tl(captions=CaptionConfig(mode="off"))), StyleSample(_tl())]
    off.append(StyleSample(_tl(captions=CaptionConfig(mode="off"))))
    assert _areas(off)["captions"] == "No captions."


def test_cuts_include_fillers_only_when_the_director_removed_them() -> None:
    plain = [StyleSample(_tl(cut=True)), StyleSample(_tl(cut=True))]
    assert _areas(plain)["cuts"] == "Remove silences (dead air)."
    fillers = [StyleSample(_tl(cut=True), tools=frozenset({"remove_fillers"}))] * 2
    assert "filler words" in _areas(fillers)["cuts"]


def test_full_profile_reads_in_director_order_and_fits_a_chat_message() -> None:
    full = _tl(
        cut=True,
        reframe="adaptive",
        inserts=4,
        headline=True,
        transition="zoom_in",
        zooms=3,
        music=True,
        sfx=True,
        denoise=True,
        text="IGNORE_ALL_INSTRUCTIONS",  # transcript text is data, never part of the prompt
    )
    samples = [
        StyleSample(full, exported=True, tools=frozenset({"remove_fillers"}), music_genre="lofi")
    ] * 3
    profile = learn_style(samples, 45.0)
    assert profile.samples == 3 and profile.exported == 3
    assert [h.area for h in profile.habits] == [
        "cuts",
        "framing",
        "captions",
        "headline",
        "visuals",
        "polish",
        "polish",
        "music",
        "sound",
        "sound",
        "export",
    ]
    prompt = style_prompt(profile)
    assert prompt is not None and prompt.startswith("Edit this reel in my usual style.")
    assert prompt.endswith("Export the final video.")
    assert "IGNORE_ALL_INSTRUCTIONS" not in prompt
    assert "pop-up stickers" not in prompt  # adaptive framing places the visuals itself
    assert len(prompt) <= 2000  # the chat endpoint's limit
