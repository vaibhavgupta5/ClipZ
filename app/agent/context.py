"""Director context: static rules + fenced project data + per-turn timeline state.

Prompt layout (cache-friendly, stable first): tools -> RULES -> project data
(fixed per analysis) -> conversation -> current timeline state (changes each turn).
Everything derived from media is UNTRUSTED DATA (rule 6): it is fenced, and angle
brackets are neutralised so transcript text cannot close or open a fence.
"""

from pathlib import Path

from app.analysis.models import FillerHit, SpeechRange
from app.timeline.schema import Timeline
from app.timeline.summary import summarize

# The Director's role, process and craft rules: a plain text file, readable and editable.
PROMPT_FILE = Path(__file__).parent / "prompts" / "director.txt"
RULES = PROMPT_FILE.read_text(encoding="utf-8").strip()


def _fence(text: str) -> str:
    """Neutralise angle brackets so untrusted text cannot forge fence tags."""
    return text.replace("<", "‹").replace(">", "›")


def _ranges(ranges: list[tuple[float, float]]) -> str:
    return ", ".join(f"{a:.2f}-{b:.2f}s" for a, b in ranges) or "none"


def _fillers(hits: list[FillerHit]) -> str:
    return ", ".join(f"{h.word_id}={_fence(h.filler)}" for h in hits) or "none"


def project_data(
    tl: Timeline,
    speech: list[SpeechRange] | None = None,
    fillers: list[FillerHit] | None = None,
    words_per_line: int = 12,
) -> str:
    """Fenced, compact project facts: transcript with IDs, VAD speech, fillers, beats."""
    words = tl.transcript.words
    lines = [
        f"@{words[i].start:.1f}s "
        + " ".join(f"[{w.id}] {_fence(w.text)}" for w in words[i : i + words_per_line])
        for i in range(0, len(words), words_per_line)
    ]
    parts = [
        f"language: {tl.transcript.language}",
        "transcript (subtitle lines, source time):",
        *(lines or ["(no speech detected)"]),
        f"speech ranges (source s): {_ranges([(s.start, s.end) for s in speech or []])}",
        f"filler candidates: {_fillers(fillers or [])}",
    ]
    if tl.transcript.beats:
        parts.append("beats:")
        parts += [
            f"{b.id} {b.intent} {b.word_ids[0]}..{b.word_ids[-1]} {_fence(b.query_hint)}"
            for b in tl.transcript.beats
        ]
    return "<project_data>\n" + "\n".join(parts) + "\n</project_data>"


def system_prompt(data: str) -> str:
    """Static rules first, then per-project data (both stable across a chat)."""
    return f"{RULES}\n\n{data}"


def timeline_state(tl: Timeline, visual_candidates: str = "") -> str:
    """Per-turn state appended to the user's message (plus keyword->visual matches)."""
    state = f"<timeline_state>\n{_fence(summarize(tl))}\n</timeline_state>"
    if visual_candidates:
        state += f"\n<visual_candidates>\n{_fence(visual_candidates)}\n</visual_candidates>"
    return state
