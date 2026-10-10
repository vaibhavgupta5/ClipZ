"""Learn a creator's editing style from their own past edits (ADR-021).

Numbers come from code, never from a model: each sample is the accepted (current) timeline
of one of the user's other reels. Categorical choices take the weighted majority, numbers the
weighted median, and an optional element (music, zooms, visuals...) joins the style only when
the user kept it in at least half of the weighted reels. Exported reels weigh double: the user
shipped them.

Only structured timeline fields are read. Transcript text never enters the prompt (rule 6):
the Director picks the moments from the new video itself.
"""

from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.timeline.schema import CaptionConfig, Overlay, Timeline

Area = Literal[
    "cuts", "framing", "captions", "headline", "visuals", "polish", "music", "sound", "export"
]
# Prompt order = the order the Director edits in (cuts first, export last).
AREAS: tuple[Area, ...] = (
    "cuts",
    "framing",
    "captions",
    "headline",
    "visuals",
    "polish",
    "music",
    "sound",
    "export",
)
HABIT_MIN_SHARE = 0.5  # an optional element is "your style" when kept in >= half the reels
KIND_MIN_SHARE = 0.15  # visual kinds below this share of all inserts are not mentioned
EXPORTED_WEIGHT = 2.0
CARD_TYPES = frozenset({"headline_card", "stat_card"})
MAX_ZOOMS = 12  # add_zooms takes at most 12 word IDs per call

FRAMING: dict[str, tuple[str, str]] = {  # mode -> (label, instruction)
    "adaptive": (
        "adaptive framing",
        "Use adaptive 9:16 framing: split screen with the visual on top whenever one shows, "
        "the speaker in the middle over a blurred background otherwise.",
    ),
    "split_bottom": (
        "split screen",
        "Use split-screen 9:16 framing: the speaker at the bottom, visuals on top.",
    ),
    "blur": ("blurred fill", "Reframe to 9:16 with a blurred background fill."),
    "face": ("face crop", "Reframe to 9:16 centred on the speaker's face."),
}
KIND_WORDS = {
    "icon": "brand/tech logos",
    "video": "B-roll clips",
    "image": "stock images",
    "gif": "reaction GIFs",
    "meme": "memes for real jokes",
}
HEADLINE_AT = {"top_banner": "at the top", "lower_third_card": "at the bottom", "pip_pop": ""}


@dataclass(frozen=True)
class StyleSample:
    """One past reel: its accepted timeline plus facts the timeline does not hold."""

    timeline: Timeline
    exported: bool = False  # a final render finished: the user shipped this edit
    tools: frozenset[str] = field(default_factory=frozenset)  # tools the Director ran
    music_genre: str | None = None  # resolved from the asset library by the caller


class Habit(BaseModel):
    """One learned habit: what the user does, and in how much of their (weighted) work."""

    model_config = ConfigDict(frozen=True)

    area: Area = Field(description="Edit stage, in Director order")
    label: str = Field(description="Short name shown in the review card")
    instruction: str = Field(description="The sentence sent to the Director")
    share: float = Field(ge=0.0, le=1.0, description="Weighted share of reels showing it")


class StyleProfile(BaseModel):
    """What was learned, ready for review."""

    model_config = ConfigDict(frozen=True)

    samples: int = Field(ge=0, description="Reels learned from")
    exported: int = Field(ge=0, description="How many of them were exported")
    habits: list[Habit] = Field(default_factory=list, description="In Director order")


class _Votes:
    """Weighted tallies over the samples (newest first, so ties go to the newest)."""

    def __init__(self, samples: Sequence[StyleSample]) -> None:
        self.samples = list(samples)
        self.weights = [EXPORTED_WEIGHT if s.exported else 1.0 for s in self.samples]
        self.total = sum(self.weights)

    def share(self, test: Callable[[StyleSample], bool]) -> float:
        """Weighted share of samples passing ``test``."""
        hit = sum(w for s, w in zip(self.samples, self.weights, strict=True) if test(s))
        return hit / self.total if self.total else 0.0

    def majority[T: Hashable](
        self, value: Callable[[StyleSample], T | None]
    ) -> tuple[T | None, float]:
        """Most common non-None value and its weighted share of ALL samples."""
        tally: dict[T, float] = {}
        for s, w in zip(self.samples, self.weights, strict=True):
            v = value(s)
            if v is not None:
                tally[v] = tally.get(v, 0.0) + w
        if not tally:
            return None, 0.0
        best = max(tally, key=lambda k: tally[k])  # first max = newest on ties
        return best, tally[best] / self.total

    def median(self, value: Callable[[StyleSample], float | None]) -> float | None:
        """Weighted median of the non-None values."""
        pairs = sorted(
            (v, w)
            for s, w in zip(self.samples, self.weights, strict=True)
            if (v := value(s)) is not None
        )
        if not pairs:
            return None
        half, acc = sum(w for _, w in pairs) / 2, 0.0
        for v, w in pairs:
            acc += w
            if acc >= half:
                return v
        return pairs[-1][0]


def _inserts(tl: Timeline) -> list[Overlay]:
    return [o for o in tl.overlays if o.type not in CARD_TYPES and o.layout != "top_banner"]


def _per_min(count: int, tl: Timeline) -> float | None:
    return count / tl.duration_s * 60.0 if count and tl.duration_s > 0 else None


def _about(rate_per_min: float, target_s: float, cap: int = 99) -> int:
    return max(1, min(cap, round(rate_per_min * target_s / 60.0)))


def _framing(s: StyleSample) -> str:
    return "split_bottom" if s.timeline.reframe == "split" else s.timeline.reframe


def _cuts(v: _Votes) -> list[Habit]:
    silences = v.share(lambda s: len(s.timeline.video_clips) > 1)
    if silences < HABIT_MIN_SHARE:
        return []
    fillers = v.share(lambda s: "remove_fillers" in s.tools)
    if fillers >= HABIT_MIN_SHARE:
        return [
            Habit(
                area="cuts",
                label="tight cuts (silences + fillers)",
                instruction="Remove silences and filler words for a tight cut.",
                share=round(min(silences, fillers), 2),
            )
        ]
    return [
        Habit(
            area="cuts",
            label="silence removal",
            instruction="Remove silences (dead air).",
            share=round(silences, 2),
        )
    ]


def _framing_habit(v: _Votes) -> tuple[str | None, list[Habit]]:
    """(learned reframe mode, its habit); center crop is the default, so nothing to ask."""
    mode, share = v.majority(_framing)
    if mode is None or mode not in FRAMING or share < HABIT_MIN_SHARE:
        return None, []
    label, instruction = FRAMING[mode]
    habit = Habit(area="framing", label=label, instruction=instruction, share=round(share, 2))
    return mode, [habit]


def _caption_text(c: CaptionConfig) -> str:
    look = [
        "word-by-word highlight" if c.mode == "word_highlight" else "line by line",
        f"{c.words_per_line} word{'s' if c.words_per_line != 1 else ''} per line",
    ]
    if c.uppercase:
        look.append("uppercase")
    if c.animation == "pop":
        look.append("pop animation")
    look.append(f"{c.size_px} px")
    colours = f"{c.base_color} text"
    if c.mode == "word_highlight":
        colours += f" with {c.highlight_color} highlight"
    look.append(colours)
    look.append("in the middle" if c.position == "center" else "in the lower third")
    return "Captions: " + ", ".join(look) + "."


def _caption_field(name: str) -> Callable[[StyleSample], Hashable]:
    def get(s: StyleSample) -> Hashable:
        value: Hashable = getattr(s.timeline.captions, name)
        return value.upper() if isinstance(value, str) and name.endswith("_color") else value

    return get


def _captions(v: _Votes) -> list[Habit]:
    mode, share = v.majority(lambda s: s.timeline.captions.mode)
    if mode is None:
        return []
    if mode == "off":
        off = Habit(area="captions", label="no captions", instruction="No captions.", share=share)
        return [off]
    on = _Votes([s for s in v.samples if s.timeline.captions.mode == mode])
    # Each field by its own majority (weighted median for the size), over captioned reels.
    fields = ("position", "words_per_line", "base_color", "highlight_color", "uppercase")
    update: dict[str, object] = {name: on.majority(_caption_field(name))[0] for name in fields}
    update["animation"] = on.majority(lambda s: s.timeline.captions.animation)[0]
    update["size_px"] = int(on.median(lambda s: float(s.timeline.captions.size_px)) or 84)
    config = on.samples[0].timeline.captions.model_copy(
        update={k: val for k, val in update.items() if val is not None}
    )
    label = f"captions {config.size_px}px" + (" caps" if config.uppercase else "")
    return [
        Habit(
            area="captions",
            label=label,
            instruction=_caption_text(config),
            share=round(share, 2),
        )
    ]


def _headline(v: _Votes) -> list[Habit]:
    def layout(s: StyleSample) -> str | None:
        return next((o.layout for o in s.timeline.overlays if o.type == "headline_card"), None)

    share = v.share(lambda s: layout(s) is not None)
    if share < HABIT_MIN_SHARE:
        return []
    where, _ = v.majority(layout)
    at = HEADLINE_AT.get(where or "", "")
    text = "Add a short headline card" + (f" {at}" if at else "") + " that sums up the hook."
    return [Habit(area="headline", label="headline card", instruction=text, share=round(share, 2))]


def _visuals(v: _Votes, target_s: float, framing: str | None) -> list[Habit]:
    share = v.share(lambda s: bool(_inserts(s.timeline)))
    if share < HABIT_MIN_SHARE:
        return []
    rate = v.median(lambda s: _per_min(len(_inserts(s.timeline)), s.timeline)) or 0.0
    kinds: dict[str, float] = {}
    layouts: dict[str, float] = {}
    for s, w in zip(v.samples, v.weights, strict=True):
        for o in _inserts(s.timeline):
            kinds[o.type] = kinds.get(o.type, 0.0) + w
            layouts[o.layout] = layouts.get(o.layout, 0.0) + w
    total = sum(kinds.values()) or 1.0
    named = [
        KIND_WORDS[k]
        for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])
        if k in KIND_WORDS and n / total >= KIND_MIN_SHARE
    ]
    text = "Show relevant visuals the moment key things are said"
    if named:
        text += f" ({', '.join(named)})"
    text += f", about {_about(rate, target_s)} across the video"
    if framing not in ("adaptive", "split_bottom") and layouts:
        top = max(layouts, key=lambda k: layouts[k])
        if layouts[top] / total >= HABIT_MIN_SHARE:
            text += " as pop-up stickers" if top == "pip_pop" else " full screen"
    return [
        Habit(
            area="visuals",
            label=f"visuals ~{rate:.0f}/min",
            instruction=text + ".",
            share=round(share, 2),
        )
    ]


def _polish(v: _Votes, target_s: float) -> list[Habit]:
    out: list[Habit] = []
    style, share = v.majority(lambda s: s.timeline.transition)
    if style is not None and style != "none" and share >= HABIT_MIN_SHARE:
        out.append(
            Habit(
                area="polish",
                label=f"{style} transitions",
                instruction=f"Add {style} transitions at real cuts.",
                share=round(share, 2),
            )
        )
    zoomed = v.share(lambda s: bool(s.timeline.zooms))
    if zoomed >= HABIT_MIN_SHARE:
        rate = v.median(lambda s: _per_min(len(s.timeline.zooms), s.timeline)) or 0.0
        scale = v.median(
            lambda s: (
                sum(z.scale for z in s.timeline.zooms) / len(s.timeline.zooms)
                if s.timeline.zooms
                else None
            )
        )
        n = _about(rate, target_s, MAX_ZOOMS)
        text = f"Add about {n} punch-in zoom{'s' if n != 1 else ''}"
        if scale is not None:
            text += f" (scale {scale:.2f})"
        out.append(
            Habit(
                area="polish",
                label=f"zooms ~{rate:.0f}/min",
                instruction=text + " on key claims, numbers and punchlines.",
                share=round(zoomed, 2),
            )
        )
    return out


def _music(v: _Votes) -> list[Habit]:
    share = v.share(lambda s: s.timeline.music is not None)
    if share < HABIT_MIN_SHARE:
        return []
    genre, _ = v.majority(lambda s: s.music_genre if s.timeline.music is not None else None)
    level = v.median(lambda s: s.timeline.music.level_db if s.timeline.music else None)
    text = f"Add {genre} background music" if genre else "Add background music that fits the mood"
    if level is not None:
        text += f" at about {level:.0f} dB under the voice"
    return [
        Habit(
            area="music",
            label=f"{genre or 'background'} music",
            instruction=text + ".",
            share=round(share, 2),
        )
    ]


def _sound(v: _Votes) -> list[Habit]:
    out: list[Habit] = []
    denoised = v.share(lambda s: any(e.type == "denoise" for e in s.timeline.audio_fx))
    if denoised >= HABIT_MIN_SHARE:
        out.append(
            Habit(
                area="sound",
                label="noise reduction",
                instruction="Reduce background noise.",
                share=round(denoised, 2),
            )
        )
    sfx = v.share(lambda s: bool(s.timeline.sfx))
    if sfx >= HABIT_MIN_SHARE:
        out.append(
            Habit(
                area="sound",
                label="synced sound effects",
                instruction="Add sound effects synced to the cuts, visuals and zooms.",
                share=round(sfx, 2),
            )
        )
    return out


def _export(v: _Votes) -> list[Habit]:
    share = v.share(lambda s: s.exported)
    if share < HABIT_MIN_SHARE:
        return []
    return [
        Habit(
            area="export",
            label="export",
            instruction="Export the final video.",
            share=round(share, 2),
        )
    ]


def learn_style(samples: Sequence[StyleSample], target_duration_s: float) -> StyleProfile:
    """Habits shared by the user's past reels (newest first), sized for the target video."""
    if not samples:
        return StyleProfile(samples=0, exported=0)
    v = _Votes(samples)
    mode, framing = _framing_habit(v)
    habits = [
        *_cuts(v),
        *framing,
        *_captions(v),
        *_headline(v),
        *_visuals(v, target_duration_s, mode),
        *_polish(v, target_duration_s),
        *_music(v),
        *_sound(v),
        *_export(v),
    ]
    habits.sort(key=lambda h: AREAS.index(h.area))
    return StyleProfile(
        samples=len(samples), exported=sum(1 for s in samples if s.exported), habits=habits
    )


def style_prompt(profile: StyleProfile) -> str | None:
    """One Director message in the user's style; None when nothing was learned."""
    if not profile.habits:
        return None
    return "Edit this reel in my usual style. " + " ".join(h.instruction for h in profile.habits)
