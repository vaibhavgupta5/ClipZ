"""ASS caption writer: per-word Dialogue events, safe-area aware, escaped text."""

from pydantic import BaseModel, ConfigDict

from app.render.fonts import font_for
from app.timeline.transcript import Word

HIGHLIGHT_YELLOW = "&H00D4FF&"  # ASS BBGGRR: FFD400 yellow
BOTTOM_CLEAR_PX = 340  # Reels UI covers the bottom strip
SIDE_MARGIN_PX = 60


class CaptionCue(BaseModel):
    """One caption event with OUTPUT-relative timing (post-cut)."""

    model_config = ConfigDict(frozen=True)

    id: str
    start: float
    end: float
    text: str


def escape_ass(text: str) -> str:
    """Escape ASS metacharacters. Colons/semicolons/emoji/RTL pass through."""
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\r\n", "\\N")
        .replace("\r", "\\N")
        .replace("\n", "\\N")
    )


def _stamp(seconds: float) -> str:
    total_cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(total_cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_for_cues(
    cues: list[CaptionCue],
    highlight: str | None = None,
    font: str = "latin",
    position: str = "center_lower",
    words_per_line: int = 1,
) -> str:
    """Build a complete ASS document from output-relative cues."""
    family, _ = font_for(font)
    alignment = 2 if position == "center_lower" else 5
    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: 1080",
            "PlayResY: 1920",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Pop,{family},84,&H00FFFFFF,&H000019FF,&H80000000,&H00000000,"
            f"-1,0,0,0,100,100,0,0,1,6,2,{alignment},{SIDE_MARGIN_PX},{SIDE_MARGIN_PX},"
            f"{BOTTOM_CLEAR_PX},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )
    lines = [header]
    groups: list[list[CaptionCue]] = []
    if words_per_line <= 1:
        groups = [[c] for c in cues]
    else:
        for i in range(0, len(cues), words_per_line):
            groups.append(cues[i : i + words_per_line])
    for group in groups:
        start = _stamp(group[0].start)
        end = _stamp(max(c.end for c in group))
        parts: list[str] = []
        for cue in group:
            text = escape_ass(cue.text)
            if highlight is not None and cue.id == highlight:
                text = "{\\c" + HIGHLIGHT_YELLOW + "}" + text
            parts.append(text)
        lines.append(f"Dialogue: 0,{start},{end},Pop,,0,0,0,,{' '.join(parts)}")
    return "\n".join(lines) + "\n"


def ass_for_words(
    words: list[Word],
    highlight: str | None = None,
    font: str = "latin",
    position: str = "center_lower",
    words_per_line: int = 1,
) -> str:
    """Source-time wrapper (correct for uncut timelines; renderer uses cues)."""
    cues = [CaptionCue(id=w.id, start=w.start, end=w.end, text=w.display_text) for w in words]
    return ass_for_cues(cues, highlight, font, position, words_per_line)
