"""Request coverage: which parts of the user's own message were not acted on.

Small models often drop items from long multi-part requests ("...and add images").
This deterministic check maps request phrases to the tools that satisfy them, so the
Director can give the model one targeted follow-up. Only the user's chat message is
read, never transcript or media text (rule 6), so spoken words cannot trigger tools.
"""

import re
from dataclasses import dataclass

from app.timeline.schema import Timeline

_NEGATION = r"\b(?:no|without|don'?t|skip|never)\s+(?:\w+\s+){0,2}"


@dataclass(frozen=True)
class Requirement:
    """A request phrase and the tools that satisfy it."""

    label: str
    pattern: str
    tools: frozenset[str]


REQUIREMENTS = (
    Requirement(
        "images / memes / GIFs",
        r"\b(?:images?|photos?|pictures?|memes?|gifs?|stickers?)\b",
        frozenset({"add_inserts", "auto_visuals", "insert_asset", "insert_meme"}),
    ),
    Requirement("background music", r"\b(?:music|soundtrack|bgm)\b", frozenset({"add_music"})),
    Requirement(
        "captions",
        r"\b(?:captions?|subtitles?)\b",
        frozenset({"add_captions", "set_caption_style"}),
    ),
    Requirement(
        "punch-in zooms", r"\b(?:zoom(?:s|ing)?|punch[- ]?ins?)\b", frozenset({"add_zooms"})
    ),
    Requirement("cut transitions", r"\btransitions?\b", frozenset({"set_transition"})),
    Requirement(
        "relevant visuals",
        r"\b(?:b-?rolls?|visuals?|logos?|icons?)\b",
        frozenset({"add_inserts", "auto_visuals", "insert_asset"}),
    ),
    Requirement(
        "sound effects",
        r"\b(?:sound effects?|audio effects?|sfx|whoosh(?:es)?|swoosh(?:es)?)\b",
        frozenset({"auto_sfx", "add_sfx", "generate_sfx"}),
    ),
    Requirement("noise reduction", r"\b(?:noise|denoise|hiss)\b", frozenset({"denoise"})),
    Requirement(
        "silence removal",
        r"\b(?:silences?|dead air|pauses|smart cuts?)\b",
        frozenset({"remove_silence"}),
    ),
    Requirement(
        "filler removal",
        r"\bfillers?(?: words?)?\b|\bums?\b|\buhs?\b",
        frozenset({"remove_fillers"}),
    ),
    Requirement(
        "vertical 9:16 framing", r"\b(?:9:16|vertical|reframe)\b", frozenset({"reframe_9x16"})
    ),
    Requirement("export", r"\bexport\b", frozenset({"export"})),
)


def requested(message: str) -> list[Requirement]:
    """Requirements the message asks for (negated mentions like 'no music' excluded)."""
    text = message.lower()
    out = []
    for req in REQUIREMENTS:
        hits = list(re.finditer(req.pattern, text))
        if not hits:
            continue
        negated = all(
            re.search(_NEGATION + r"$", text[max(0, m.start() - 40) : m.start()]) for m in hits
        )
        if not negated:
            out.append(req)
    return out


def missing(message: str, applied: set[str], timeline: Timeline) -> list[str]:
    """Labels of requested items that no applied tool satisfied."""
    gaps = []
    for req in requested(message):
        done = bool(req.tools & applied)
        if req.label == "cut transitions" and timeline.transition == "none":
            done = False  # "smooth transitions" is not satisfied by style=none
        if not done:
            gaps.append(req.label)
    return gaps


# Requests whose fix needs no creative choice: if the model still skips them after the
# follow-up note, the Director applies them itself with these defaults.
_GENRES = (
    (r"\b(?:motivat\w*|inspir\w*|hustle|grind|gym|success)\b", "electronic"),
    (r"\b(?:sad|depress\w*|emotional|dark|moody|serious)\b", "ambient"),
    (r"\b(?:epic|dramatic|cinematic|trailer)\b", "cinematic"),
    (r"\b(?:birthday|party|celebrat\w*|happy|fun|wedding)\b", "upbeat"),
    (r"\b(?:tech|coding|code|ai|startup|software|cyberpunk|futur\w*)\b", "electronic"),
    (r"\b(?:chill|study|calm|relax\w*|tutorial|vlog)\b", "lofi"),
    (r"\b(?:business|finance|money|corporate|product)\b", "corporate"),
    (r"\b(?:rap|street|hip ?hop)\b", "hiphop"),
)


def music_genre(message: str, transcript_text: str = "", available: list[str] | None = None) -> str:
    """Genre for a default music bed: the request's mood first, then the video's topic,
    limited to the installed genres."""
    ok = set(available) if available else {g for _, g in _GENRES} | {"upbeat"}
    for text in (message.lower(), transcript_text.lower()):
        for pattern, genre in _GENRES:
            if genre in ok and re.search(pattern, text):
                return genre
    return "upbeat" if "upbeat" in ok else sorted(ok)[0]


def autofix_calls(
    gaps: list[str],
    message: str,
    transcript_text: str = "",
    genres: list[str] | None = None,
) -> list[tuple[str, dict[str, object]]]:
    """Deterministic (tool, args) for gaps that have a safe default; others stay with the model."""
    defaults: dict[str, tuple[str, dict[str, object]]] = {
        "background music": (
            "add_music",
            {"genre": music_genre(message, transcript_text, genres)},
        ),
        "captions": ("add_captions", {}),
        "noise reduction": ("denoise", {"enabled": True}),
        "vertical 9:16 framing": ("reframe_9x16", {"mode": "blur"}),
        "silence removal": ("remove_silence", {}),
    }
    calls = [defaults[g] for g in gaps if g in defaults]
    # sound effects go last: they sync to the cuts and inserts placed before them
    if "sound effects" in gaps:
        calls.append(("auto_sfx", {}))
    return calls
