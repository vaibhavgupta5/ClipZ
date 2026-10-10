"""Timeline EDL schema: frozen pydantic models. Duration in ms precision."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.timeline.transcript import Transcript

Frozen = ConfigDict(frozen=True)

WORD_ID = r"^w\d+$"
HEX_COLOR = r"^#[0-9A-Fa-f]{6}$"
MIN_SPEED, MAX_SPEED = 0.25, 4.0
EPS = 1e-6  # float dust threshold for clip-boundary comparisons


class VideoClip(BaseModel):
    """One source range on the video track. Output length = (src_out - src_in) / speed."""

    model_config = Frozen

    id: str = Field(min_length=1, description="Clip ID, unique per timeline")
    asset: str = Field(min_length=1, description="Source asset ID")
    src_in: float = Field(ge=0.0, description="Source in-point seconds")
    src_out: float = Field(ge=0.0, description="Source out-point seconds")
    speed: float = Field(
        default=1.0, gt=0, description="Speed factor (policy range enforced by validator)"
    )

    @model_validator(mode="after")
    def _ordered(self) -> "VideoClip":
        if self.src_out <= self.src_in:
            raise ValueError(f"clip {self.id}: src_out must exceed src_in")
        return self

    @property
    def output_len(self) -> float:
        """Output seconds contributed by this clip."""
        return (self.src_out - self.src_in) / self.speed


class AudioEffect(BaseModel):
    """Declarative audio effect (denoise, loudnorm, ducking configured in Phase 6)."""

    model_config = Frozen

    type: Literal["denoise", "loudnorm", "ducking"] = Field(description="Effect type")
    params: dict[str, float] = Field(default_factory=dict, description="Effect params")


class CaptionConfig(BaseModel):
    """Caption rendering config (ASS renderer lands in Phase 4)."""

    model_config = Frozen

    mode: Literal["word_highlight", "line", "off"] = Field(default="word_highlight")
    position: Literal["center", "center_lower"] = Field(
        default="center_lower", description="Safe-area aware position (never bottom strip)"
    )
    words_per_line: int = Field(
        default=2, ge=1, description="Words per line (policy cap enforced by validator)"
    )
    style: str = Field(default="from_profile", description="Caption style / preset name")
    size_px: int = Field(default=84, ge=40, le=160, description="Font size at 1080x1920")
    base_color: str = Field(default="#FFFFFF", pattern=HEX_COLOR, description="Base text colour")
    highlight_color: str = Field(
        default="#FFD400", pattern=HEX_COLOR, description="Active-word colour (word_highlight)"
    )
    uppercase: bool = Field(default=False, description="Render caption text in capitals")
    animation: Literal["none", "pop"] = Field(
        default="none", description="pop = active word scales 85% -> 112% -> 100%"
    )


class Zoom(BaseModel):
    """Punch-in anchored to a word ID."""

    model_config = Frozen

    id: str = Field(min_length=1, description="Zoom ID, unique per timeline")
    anchor_word: str = Field(pattern=WORD_ID, description="Word ID the zoom lands on")
    scale: float = Field(gt=0, description="Zoom scale (policy range enforced by validator)")
    dur_ms: int = Field(default=220, ge=100, le=2000, description="Zoom duration")


class Overlay(BaseModel):
    """Visual insert anchored to a word ID (cutaway, PiP, card...)."""

    model_config = Frozen

    id: str = Field(min_length=1, description="Insert ID, unique per timeline")
    type: Literal["image", "gif", "meme", "stat_card", "headline_card"] = Field(
        description="Asset type"
    )
    asset: str = Field(min_length=1, description="Asset ID")
    anchor_word: str = Field(pattern=WORD_ID, description="Anchor: end of this word + offset")
    offset_ms: int = Field(default=80, ge=0, le=150, description="Delay after word end")
    duration_ms: int = Field(ge=300, le=3000, description="On-screen duration")
    layout: Literal[
        "cutaway_full", "pip_pop", "split_top", "lower_third_card", "blur_bg_contain"
    ] = Field(description="Layout mode")
    license: str = Field(default="", description="Asset license (presence enforced by validator)")
    attribution_required: bool = Field(default=False)
    attribution_text: str = Field(default="")
    commercial_use: bool = Field(default=True, description="False = personal use only (ADR-014)")


class MusicBed(BaseModel):
    """Burned-in music bed (provider + ducking land in Phase 6)."""

    model_config = Frozen

    asset: str = Field(min_length=1, description="Music asset ID")
    license: str = Field(default="", description="Track license (presence enforced by validator)")
    attribution_required: bool = Field(default=False)
    attribution_text: str = Field(default="")
    level_db: float = Field(default=-18.0, le=0.0, description="Bed level under voice")
    commercial_use: bool = Field(default=True, description="False = personal use only (ADR-014)")


class SfxHit(BaseModel):
    """One sound-effect hit anchored to a word."""

    model_config = Frozen

    id: str = Field(min_length=1, description="SFX ID")
    asset: str = Field(min_length=1, description="SFX asset ID")
    anchor_word: str = Field(pattern=WORD_ID, description="Anchor word")
    offset_ms: int = Field(default=0, ge=-500, le=500, description="Offset from word end")
    gain_db: float = Field(default=-8.0, le=0.0, description="Hit gain")
    license: str = Field(default="", description="Clip license (presence enforced by validator)")


class OutputSpec(BaseModel):
    """Render target (always vertical)."""

    model_config = Frozen

    aspect: Literal["9:16"] = Field(default="9:16")
    width: int = Field(default=1080)
    height: int = Field(default=1920)
    fps: int = Field(default=30, ge=24, le=60)


class Timeline(BaseModel):
    """Immutable EDL snapshot. Every op returns a new Timeline + Diff."""

    model_config = Frozen

    project_id: uuid.UUID = Field(description="Owning project")
    version: int = Field(default=1, ge=1, description="Version number this snapshot represents")
    transcript: Transcript = Field(description="Canonical words + beats")
    video_clips: list[VideoClip] = Field(min_length=1, description="Ordered video track")
    audio_fx: list[AudioEffect] = Field(default_factory=list)
    captions: CaptionConfig = Field(default_factory=CaptionConfig)
    zooms: list[Zoom] = Field(default_factory=list)
    overlays: list[Overlay] = Field(default_factory=list)
    music: MusicBed | None = Field(default=None)
    sfx: list[SfxHit] = Field(default_factory=list)
    output: OutputSpec = Field(default_factory=OutputSpec)
    style_id: str | None = Field(default=None)
    trend_pack: str | None = Field(default=None)
    reframe: Literal["center", "face", "blur"] = Field(
        default="center", description="9:16 reframe mode (face falls back to center without tracks)"
    )

    @field_validator("video_clips")
    @classmethod
    def _unique_clip_ids(cls, clips: list[VideoClip]) -> list[VideoClip]:
        ids = [c.id for c in clips]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate clip ids")
        return clips

    @field_validator("overlays")
    @classmethod
    def _unique_overlay_ids(cls, overlays: list[Overlay]) -> list[Overlay]:
        ids = [o.id for o in overlays]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate overlay ids")
        return overlays

    @property
    def duration_s(self) -> float:
        """Output duration in seconds, millisecond precision."""
        return round(sum(c.output_len for c in self.video_clips), 3)


class Diff(BaseModel):
    """What one op changed (for chat messages + version history)."""

    model_config = Frozen

    op: str = Field(description="Op name")
    details: dict[str, str] = Field(default_factory=dict, description="Stringified change summary")
