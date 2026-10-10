"""ASS caption writer: per-word Dialogue events, safe-area aware, escaped text."""

import re

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


_HEX = re.compile(r"^#([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})$")


def ass_color(hex_rgb: str) -> str:
    """#RRGGBB -> ASS &H00BBGGRR&. Rejects anything else (no raw text reaches ASS)."""
    m = _HEX.match(hex_rgb)
    if m is None:
        raise ValueError(f"bad colour {hex_rgb!r}: expected #RRGGBB")
    r, g, b = (x.upper() for x in m.groups())
    return f"&H00{b}{g}{r}&"


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
    *,
    mode: str = "line",
    size_px: int = 84,
    base_color: str = "#FFFFFF",
    highlight_color: str = "#FFD400",
    uppercase: bool = False,
    animation: str = "none",
) -> str:
    """Build a complete ASS document from output-relative cues.

    mode="word_highlight" emits one event per word with that word recoloured;
    mode="line" emits one event per line (``highlight`` still recolours one cue).
    """
    family, _ = font_for(font)
    if not 40 <= size_px <= 160:
        raise ValueError(f"caption size {size_px} outside 40..160")
    primary = ass_color(base_color).rstrip("&")
    hl = ass_color(highlight_color)
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
            f"Style: Pop,{family},{size_px},{primary},&H000019FF,&H80000000,&H00000000,"
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
        group_end = max(c.end for c in group)
        if mode == "word_highlight":
            for k, active in enumerate(group):
                end_t = group[k + 1].start if k + 1 < len(group) else group_end
                text = _line(group, active.id, hl, uppercase, pop=animation == "pop")
                lines.append(
                    f"Dialogue: 0,{_stamp(active.start)},{_stamp(end_t)},Pop,,0,0,0,,{text}"
                )
            continue
        text = _line(group, highlight, hl, uppercase)
        lines.append(f"Dialogue: 0,{_stamp(group[0].start)},{_stamp(group_end)},Pop,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


POP = "\\fscx85\\fscy85\\t(0,90,\\fscx112\\fscy112)\\t(90,160,\\fscx100\\fscy100)"


def _line(
    group: list[CaptionCue],
    active: str | None,
    colour: str,
    uppercase: bool = False,
    pop: bool = False,
) -> str:
    """One caption line; the active cue is recoloured (and popped), then reset."""
    parts: list[str] = []
    for cue in group:
        text = escape_ass(cue.text.upper() if uppercase else cue.text)
        if active is not None and cue.id == active:
            text = "{" + (POP if pop else "") + "\\c" + colour + "}" + text + "{\\r}"
        parts.append(text)
    return " ".join(parts)


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
