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
    uppercase: bool = Field(default=False, description="CAPITALS (punchy reel style)")
    animation: Literal["none", "pop"] = Field(default="none", description="pop = word bounce")


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
    monetized: bool = Field(
        default=True,
        description="False only when the user says the reel will not be monetised; "
        "personal-use assets (popular meme templates) are blocked from monetised exports",
    )


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


class SearchAssetsIn(BaseModel):
    """Search the local licensed libraries."""

    model_config = Frozen

    kind: Literal["image", "gif", "meme"] = Field(
        description="image = stock photo, gif = animated reaction sticker, meme = popular template"
    )
    query: str = Field(min_length=1, max_length=80, description="2-4 words, e.g. 'money burning'")
    limit: int = Field(default=5, ge=1, le=8)


class AssetHit(BaseModel):
    """One search result."""

    model_config = Frozen

    id: str
    title: str
    tags: list[str]
    license: str
    commercial_use: bool


class SearchAssetsOut(BaseModel):
    """Search results (empty = nothing relevant; then skip the insert)."""

    model_config = Frozen

    results: list[AssetHit]


class InsertAssetIn(BaseModel):
    """Place a library image/GIF at the end of a spoken word."""

    model_config = Frozen

    asset_id: str = Field(description="id from search_assets")
    at_word: str = Field(pattern=r"^w\d+$", description="Insert appears as this word ends")
    layout: (
        Literal["cutaway_full", "pip_pop", "split_top", "lower_third_card", "blur_bg_contain"]
        | None
    ) = Field(default=None, description="Default: gif -> pip_pop, image/meme -> blur_bg_contain")
    duration_ms: int | None = Field(default=None, ge=600, le=3000)


class InsertMemeIn(BaseModel):
    """Caption a popular meme template and place it at a punchline."""

    model_config = Frozen

    template_id: str = Field(description="meme id from search_assets(kind='meme')")
    top_text: str = Field(default="", max_length=60, description="<= 6 words")
    bottom_text: str = Field(default="", max_length=60, description="<= 6 words")
    at_word: str = Field(pattern=r"^w\d+$")
    duration_ms: int | None = Field(default=None, ge=1000, le=3000)


class InsertPlanItem(BaseModel):
    """One planned insert."""

    model_config = Frozen

    at_word: str = Field(pattern=r"^w\d+$", description="Appears as this word ends")
    kind: Literal["image", "gif", "meme"]
    query: str = Field(min_length=1, max_length=80, description="2-4 concrete words")
    top_text: str = Field(default="", max_length=60, description="meme only, <= 6 words")
    bottom_text: str = Field(default="", max_length=60, description="meme only, <= 6 words")


class AddInsertsIn(BaseModel):
    """Plan several inserts in one call; each is matched, placed or skipped with a reason."""

    model_config = Frozen

    items: list[InsertPlanItem] = Field(min_length=1, max_length=8)


class InsertsOut(BaseModel):
    """What was placed and what was skipped (and why)."""

    model_config = Frozen

    version: int
    op: str = "add_inserts"
    placed: list[dict[str, str]]
    skipped: list[dict[str, str]]
    summary: str


class AddMusicIn(BaseModel):
    """Background music bed from the royalty-free library."""

    model_config = Frozen

    genre: (
        Literal[
            "upbeat",
            "lofi",
            "cinematic",
            "electronic",
            "hiphop",
            "acoustic",
            "corporate",
            "ambient",
        ]
        | None
    ) = Field(default=None, description="upbeat suits birthdays, parties, celebrations")
    mood: str = Field(default="", max_length=60, description="e.g. 'happy party', 'chill'")
    level_db: float = Field(default=-20.0, ge=-30.0, le=-10.0, description="bed under the voice")


class MusicOut(BaseModel):
    """Chosen track."""

    model_config = Frozen

    version: int
    op: str = "add_music"
    track: str
    genre: str
    license: str
    attribution: str
    summary: str


class AddZoomsIn(BaseModel):
    """Punch-in zooms that land on key words (claims, numbers, punchlines)."""

    model_config = Frozen

    word_ids: list[str] = Field(min_length=1, max_length=12, description="Word IDs to punch in on")
    scale: float = Field(default=1.12, ge=1.05, le=1.25, description="1.08 subtle .. 1.2 strong")
    hold_ms: int = Field(default=900, ge=200, le=3000, description="Time held before easing out")


class ZoomsOut(BaseModel):
    """Placed and skipped zooms."""

    model_config = Frozen

    version: int
    op: str = "add_zooms"
    placed: list[str]
    skipped: list[dict[str, str]]
    summary: str


class SetTransitionIn(BaseModel):
    """Cut style applied at every cut."""

    model_config = Frozen

    style: Literal["none", "zoom_in", "flash", "whip"] = Field(
        description="zoom_in = energetic punch, whip = fast slide, flash = white flash"
    )


SfxType = Literal[
    "whoosh", "swoosh", "pop", "ding", "riser", "boom", "click",
    "record_scratch", "camera", "typing", "notification", "glitch",
]  # fmt: skip


class AddSfxIn(BaseModel):
    """One sound effect as a word ends (offset shifts it, e.g. -150 to lead a reveal)."""

    model_config = Frozen

    sfx_type: SfxType
    at_word: str = Field(pattern=r"^w\d+$")
    offset_ms: int = Field(default=0, ge=-500, le=500)
    gain_db: float = Field(default=-12.0, ge=-30.0, le=0.0)


class AutoSfxIn(BaseModel):
    """Deterministic sound design synced to the edit."""

    model_config = Frozen

    on_cuts: bool = Field(default=True, description="whoosh on every cut")
    on_inserts: bool = Field(
        default=True, description="pop on stickers, swoosh on images, scratch on memes"
    )
    on_zooms: bool = Field(default=True, description="subtle click as each punch-in lands")
    gain_db: float = Field(
        default=-12.0, ge=-30.0, le=-3.0, description="base level under the voice"
    )


class GenerateSfxIn(BaseModel):
    """A custom sound effect generated from a description (ElevenLabs), as a word ends."""

    model_config = Frozen

    prompt: str = Field(
        min_length=3,
        max_length=120,
        description="What it sounds like, e.g. 'cash register cha-ching', 'crowd gasp'",
    )
    at_word: str = Field(pattern=r"^w\d+$")
    offset_ms: int = Field(default=0, ge=-500, le=500)
    duration_s: float = Field(default=1.0, ge=0.5, le=5.0, description="Sound length")
    gain_db: float = Field(default=-10.0, ge=-30.0, le=0.0)


class AddVoiceoverIn(BaseModel):
    """Narration spoken by a generated voice (ElevenLabs), starting as a word ends."""

    model_config = Frozen

    text: str = Field(
        min_length=2, max_length=300, description="Exact words to speak (en or hi), one idea"
    )
    at_word: str = Field(pattern=r"^w\d+$", description="Starts when this word ends")
    offset_ms: int = Field(default=0, ge=0, le=500)
    duck_db: float = Field(
        default=-14.0, ge=-40.0, le=0.0, description="Original voice level under the narration"
    )


class RemoveVoiceoverIn(BaseModel):
    """Remove a voiceover by id (vo1, vo2...)."""

    model_config = Frozen

    voiceover_id: str = Field(pattern=r"^vo\d+$")


class DubVideoIn(BaseModel):
    """Dub the speech into another language (ElevenLabs Dubbing), keeping the edit."""

    model_config = Frozen

    target_language: Literal["en", "hi", "original"] = Field(
        description="'original' removes the dub and restores the speaker's own voice"
    )


class DubOut(BaseModel):
    """Dub job queued (or dub removed)."""

    model_config = Frozen

    op: str = "dub_video"
    job_id: str = ""
    version: int = 0
    note: str = ""


class SfxOut(BaseModel):
    """Sound effects placed."""

    model_config = Frozen

    version: int
    op: str = "auto_sfx"
    placed: list[str]
    summary: str


class RemoveInsertIn(BaseModel):
    """Remove an insert by id (ins1, ins2...)."""

    model_config = Frozen

    insert_id: str = Field(min_length=1)


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
        name="search_assets",
        description="Find licensed images, animated reaction GIFs or meme templates in the "
        "local libraries. Use before insert_asset / insert_meme.",
        input_model=SearchAssetsIn,
        output_model=SearchAssetsOut,
        mutating=False,
    ),
    ToolDef(
        name="insert_asset",
        description="Show a library image or GIF as a word ends. Placement rules are enforced: "
        "not in the first second, >= 2 s apart, inside the video.",
        input_model=InsertAssetIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="insert_meme",
        description="Caption a meme template with short top/bottom text and show it at a "
        "punchline. Memes are personal-use only (blocked from monetised exports).",
        input_model=InsertMemeIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="add_inserts",
        description="Best way to add images, reaction GIFs and memes: give the whole plan in "
        "one call (word, kind, query, meme text). Each item is matched in the licensed "
        "libraries and placed by the rules, or skipped with a reason.",
        input_model=AddInsertsIn,
        output_model=InsertsOut,
        needs_preview=True,
    ),
    ToolDef(
        name="add_music",
        description="Add a royalty-free background music bed (ducked under speech, looped, "
        "faded out). Pick the genre that fits the video's mood.",
        input_model=AddMusicIn,
        output_model=MusicOut,
        needs_preview=True,
    ),
    ToolDef(
        name="remove_music",
        description="Remove the background music.",
        input_model=EmptyIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="add_zooms",
        description="Punch-in zooms on key words (claims, numbers, punchlines). Rules are "
        "enforced: >= 2.5 s apart, at most one per 4 s, never under a full-screen insert.",
        input_model=AddZoomsIn,
        output_model=ZoomsOut,
        needs_preview=True,
    ),
    ToolDef(
        name="set_transition",
        description="Professional cut transitions applied at every cut (after smart cuts).",
        input_model=SetTransitionIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="add_sfx",
        description="Add one sound effect (whoosh, pop, ding, riser, boom, click, "
        "record_scratch, camera, typing, notification, glitch) as a word ends.",
        input_model=AddSfxIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="auto_sfx",
        description="Sound design synced to the edit: whoosh on cuts, pop on stickers, "
        "swoosh on image cutaways, record scratch on memes, click on zooms. Call it after "
        "cuts, transitions, inserts and zooms are in place.",
        input_model=AutoSfxIn,
        output_model=SfxOut,
        needs_preview=True,
    ),
    ToolDef(
        name="generate_sfx",
        description="Generate a custom sound effect from a description when no library type "
        "fits (cha-ching, crowd gasp, laugh track, door slam). Prefer add_sfx/auto_sfx for "
        "whooshes, pops and dings.",
        input_model=GenerateSfxIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="add_voiceover",
        description="Add narration in a natural generated voice (hook intro, recap, call to "
        "action). The speaker's voice is ducked under it. Keep it short; it must fit before "
        "the video ends.",
        input_model=AddVoiceoverIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="remove_voiceover",
        description="Remove a voiceover by id.",
        input_model=RemoveVoiceoverIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="dub_video",
        description="Dub the speaker into English or Hindi with their voice cloned, keeping "
        "every cut and effect. Runs in the background (about a minute); the dubbed version "
        "appears as a new timeline version. 'original' removes the dub.",
        input_model=DubVideoIn,
        output_model=DubOut,
    ),
    ToolDef(
        name="remove_insert",
        description="Remove an image/GIF/meme insert by id.",
        input_model=RemoveInsertIn,
        output_model=OpOut,
        needs_preview=True,
    ),
    ToolDef(
        name="ask_user",
        description="Ask the user exactly one clarifying question, then stop.",
        input_model=AskUserIn,
        output_model=AskUserOut,
        mutating=False,
    ),
]
