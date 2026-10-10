"""MVP tool schemas: one pydantic in/out pair per tool (single source of truth)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Frozen = ConfigDict(frozen=True)


class OpOut(BaseModel):
    """Standard mutating-tool result."""

    model_config = Frozen

    version: int = Field(description="New timeline version")
    op: str = Field(description="Op applied")
    summary: str = Field(description="Timeline summary after the op")


class GetTimelineOut(BaseModel):
    """Read-only timeline view."""

    model_config = Frozen

    version: int = Field(description="Current version")
    summary: str = Field(description="Compact timeline summary")
    duration_s: float = Field(description="Output duration")


class EmptyIn(BaseModel):
    """No arguments."""

    model_config = Frozen


class TrimIn(BaseModel):
    """Keep a word span (pad snaps outward to word edges)."""

    model_config = Frozen

    start_word: str = Field(pattern=r"^w\d+$")
    end_word: str = Field(pattern=r"^w\d+$")
    pad_ms: int = Field(default=100, ge=0, le=500)


class SplitIn(BaseModel):
    """Split at a word start (internal use; exposed for completeness)."""

    model_config = Frozen

    at_word: str = Field(pattern=r"^w\d+$")


class DeleteWordsIn(BaseModel):
    """Delete exact words."""

    model_config = Frozen

    word_ids: list[str] = Field(min_length=1)


class ReorderIn(BaseModel):
    """Permute clips (exact permutation)."""

    model_config = Frozen

    clip_ids: list[str] = Field(min_length=1)


class SetSpeedIn(BaseModel):
    """Speed factor over a word span."""

    model_config = Frozen

    start_word: str = Field(pattern=r"^w\d+$")
    end_word: str = Field(pattern=r"^w\d+$")
    factor: float = Field(ge=0.25, le=4.0)


class RemoveSilenceIn(BaseModel):
    """Delete silence ranges at/above threshold (VAD-driven)."""

    model_config = Frozen

    min_silence_ms: int = Field(default=500, ge=300, le=5000)
    padding_ms: int = Field(default=100, ge=0, le=500)


class RemoveSilenceOut(BaseModel):
    """Silence removal result."""

    model_config = Frozen

    version: int
    op: str = "remove_silence"
    removed_ms: int = Field(description="Total silence removed")
    ranges: int = Field(description="Number of ranges cut")
    summary: str


class RemoveFillersIn(BaseModel):
    """Delete filler words (en|hi|auto from transcript)."""

    model_config = Frozen

    lang: str = Field(default="auto", description="en | hi | auto")


class RemoveFillersOut(BaseModel):
    """Filler removal result."""

    model_config = Frozen

    version: int
    op: str = "remove_fillers"
    removed: list[str] = Field(description="Deleted word IDs")
    summary: str


class AddCaptionsIn(BaseModel):
    """Word-by-word captions with style/position."""

    model_config = Frozen

    mode: Literal["word_highlight", "line", "off"] = "word_highlight"
    position: Literal["center", "center_lower"] = "center_lower"
    words_per_line: int = Field(default=2, ge=1, le=4)
    size_px: int = Field(default=84, ge=40, le=160, description="Font size; 84 is default")
    base_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$", description="#RRGGBB")
    highlight_color: str = Field(
        default="#FFD400", pattern=r"^#[0-9A-Fa-f]{6}$", description="#RRGGBB active word"
    )


class DenoiseIn(BaseModel):
    """Toggle speech denoise."""

    model_config = Frozen

    enabled: bool = True


class LoudnessIn(BaseModel):
    """Loudness normalization target."""

    model_config = Frozen

    target_lufs: float = Field(default=-14.0, ge=-24.0, le=-8.0)


class ReframeIn(BaseModel):
    """9:16 reframe mode."""

    model_config = Frozen

    mode: Literal["center", "face", "blur"] = "center"


class PreviewIn(BaseModel):
    """Render a preview (full timeline or word range, ephemeral)."""

    model_config = Frozen

    start_word: str | None = Field(default=None, pattern=r"^w\d+$")
    end_word: str | None = Field(default=None, pattern=r"^w\d+$")


class PreviewOut(BaseModel):
    """Preview render enqueued."""

    model_config = Frozen

    render_id: str
    job_id: str
    profile: str = "preview"


class ExportIn(BaseModel):
    """Render the final export."""

    model_config = Frozen

    profile: Literal["preview", "final"] = "final"


class ExportOut(BaseModel):
    """Export render enqueued."""

    model_config = Frozen

    render_id: str
    job_id: str
    profile: str


class AskUserIn(BaseModel):
    """Ask the user exactly one question (ambiguity escape hatch)."""

    model_config = Frozen

    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list, max_length=4)


class AskUserOut(BaseModel):
    """Echo of the asked question (director stops and waits)."""

    model_config = Frozen

    question: str
    options: list[str] = []


class ToolDef(BaseModel):
    """One registered tool: names, schemas, and kind."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    mutating: bool = True
    needs_preview: bool = False


TOOLS: list[ToolDef] = [
    ToolDef(
        name="get_timeline",
        description="Read the current timeline (version, summary, duration). Never edits.",
        input_model=EmptyIn,
        output_model=GetTimelineOut,
        mutating=False,
    ),
    ToolDef(
        name="undo",
        description="Undo: pointer back one version.",
        input_model=EmptyIn,
        output_model=OpOut,
        mutating=False,
    ),
    ToolDef(
        name="redo",
        description="Redo along the linear chain.",
        input_model=EmptyIn,
        output_model=OpOut,
        mutating=False,
    ),
    ToolDef(
        name="trim",
        description="Keep only a word span (pad snaps outward to word edges).",
        input_model=TrimIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="delete_range",
        description="Delete a padded word span.",
        input_model=TrimIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="cut_by_transcript",
        description="Keep a word span (same as trim).",
        input_model=TrimIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="remove_silence",
        description="Delete VAD silence at/above threshold.",
        input_model=RemoveSilenceIn,
        output_model=RemoveSilenceOut,
        needs_preview=True,
    ),
    ToolDef(
        name="remove_fillers",
        description="Delete filler words for a language.",
        input_model=RemoveFillersIn,
        output_model=RemoveFillersOut,
        needs_preview=True,
    ),
    ToolDef(
        name="add_captions",
        description="Word-by-word captions with style and position.",
        input_model=AddCaptionsIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="set_caption_style",
        description="Patch caption style/position/words-per-line.",
        input_model=AddCaptionsIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="denoise",
        description="Toggle speech denoise in the audio chain.",
        input_model=DenoiseIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="normalize_loudness",
        description="Set loudness normalization target (rendered to LUFS).",
        input_model=LoudnessIn,
        output_model=OpOut,
    ),
    ToolDef(
        name="reframe_9x16",
        description="9:16 reframe mode (center/face/blur).",
        input_model=ReframeIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="preview",
        description="Render a preview (full timeline or word range).",
        input_model=PreviewIn,
        output_model=PreviewOut,
        mutating=False,
    ),
    ToolDef(
        name="export",
        description="Render the final export file.",
        input_model=ExportIn,
        output_model=ExportOut,
        mutating=False,
    ),
    ToolDef(
        name="ask_user",
        description="Ask the user exactly one clarifying question, then stop.",
        input_model=AskUserIn,
        output_model=AskUserOut,
        mutating=False,
    ),
]
