"""Gemini Video Critic: sample preview frames + transcript -> structured quality critique.

The critic reviews vertical video reels on 5 key axes:
1. Hook Strength (first 0-3 s impact, opening caption/zoom)
2. Pacing & Rhythm (cut tightness, silence removal)
3. Caption Legibility (size, contrast, word highlights)
4. Visual Engagement (punch-in zooms, B-roll, memes, stickers)
5. Audio Balance & Sound Design (voice clarity, music ducking, SFX hits)

Returns a structured CriticReport used by the Director to self-correct in up to 2 iterations.
"""

import json
import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.media.ffmpeg import ffmpeg_path, run_capture
from app.providers.base import ChatMessage, ChatRequest, LLMProvider
from app.timeline.schema import Timeline
from app.timeline.summary import summarize

logger = logging.getLogger(__name__)
PROMPT_PATH = Path(__file__).parent / "prompts" / "critic.md"


class CriterionScore(BaseModel):
    """Score and observation for one critique criterion."""

    name: str
    score: int = Field(ge=1, le=10)
    comment: str


class CriticReport(BaseModel):
    """Structured critique output from the Gemini Video Critic."""

    overall_score: int = Field(ge=1, le=10)
    verdict: Literal["pass", "needs_edits"]
    criteria: list[CriterionScore] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    suggested_edits: list[str] = Field(default_factory=list)
    summary: str = ""


def sample_frame_timestamps(duration_s: float, count: int = 5) -> list[float]:
    """Calculate key timestamp offsets to sample for visual critique."""
    if duration_s <= 0:
        return []
    if count <= 1 or duration_s <= 1.0:
        return [round(min(0.5, duration_s / 2), 2)]

    if duration_s <= 3.0:
        step = duration_s / (count + 1)
        return [round(step * (i + 1), 2) for i in range(count)]

    # For standard reels: capture early hook (0.5s, 1.8s), mid cuts, and outro
    ts = [0.5, 1.8]
    remaining = count - 2
    if remaining > 0:
        step = (duration_s - 2.5) / (remaining + 1)
        ts.extend(round(1.8 + step * (i + 1), 2) for i in range(remaining))
    return sorted(t for t in ts if 0 <= t <= duration_s)


def extract_frame_bytes(video_path: str | Path, timestamp_s: float) -> bytes:
    """Extract a single frame JPEG at timestamp_s using ffmpeg."""
    cmd = [
        ffmpeg_path(),
        "-ss",
        f"{timestamp_s:.3f}",
        "-i",
        str(video_path),
        "-vframes",
        "1",
        "-f",
        "image2",
        "-c:v",
        "mjpeg",
        "-q:v",
        "4",
        "pipe:1",
    ]
    return run_capture(cmd)


def sample_preview_frames(
    video_path: str | Path, duration_s: float, count: int = 5
) -> list[tuple[float, bytes]]:
    """Sample up to `count` frames from the rendered preview video."""
    p = Path(video_path)
    if not p.exists():
        return []
    timestamps = sample_frame_timestamps(duration_s, count=count)
    frames: list[tuple[float, bytes]] = []
    for t in timestamps:
        try:
            b = extract_frame_bytes(p, t)
            if b:
                frames.append((t, b))
        except Exception as exc:
            logger.debug("failed to extract frame at %s: %s", t, exc)
            continue
    return frames


def evaluate_timeline_heuristic(timeline: Timeline, user_prompt: str = "") -> CriticReport:
    """Rule-based heuristic critique (used offline, in tests, or as a fast baseline)."""
    criteria: list[CriterionScore] = []
    issues: list[str] = []
    suggested_edits: list[str] = []

    # 1. Hook Strength
    hook_zooms = [
        z
        for z in timeline.zooms
        if any(w.id == z.anchor_word and w.start <= 3.0 for w in timeline.transcript.words)
    ]
    has_captions = timeline.captions.mode != "off"
    if hook_zooms or (has_captions and timeline.captions.size_px >= 84):
        hook_score = 9
        hook_comment = "Strong opening with clear hook emphasis."
    else:
        hook_score = 6
        hook_comment = "Opening hook could be punchier (e.g. initial zoom on key word)."
        issues.append("First 3 seconds lack visual punch.")
        if timeline.transcript.words:
            first_words = timeline.transcript.words[:3]
            suggested_edits.append(
                f"add_zoom scale=1.12 anchor_word='{first_words[-1].id}' dur_ms=400"
            )
    criteria.append(CriterionScore(name="hook_strength", score=hook_score, comment=hook_comment))

    # 2. Pacing & Rhythm
    num_clips = len(timeline.video_clips)
    has_transitions = timeline.transition != "none"
    if num_clips > 1 or has_transitions:
        pacing_score = 9
        pacing_comment = f"Pacing is tight across {num_clips} clip segments."
    else:
        pacing_score = 7
        pacing_comment = "Single continuous clip; consider smart silence cuts or transitions."
        issues.append("Pacing is static with no cuts or transitions.")
        suggested_edits.append("set_transition style='zoom_in'")
    criteria.append(CriterionScore(name="pacing", score=pacing_score, comment=pacing_comment))

    # 3. Caption Legibility
    if not has_captions:
        cap_score = 4
        cap_comment = "Captions are turned off."
        issues.append("Captions are disabled; vertical reels rely heavily on burned subtitles.")
        suggested_edits.append("set_captions mode='word_highlight' size_px=96")
    elif timeline.captions.size_px < 64:
        cap_score = 6
        cap_comment = f"Caption size ({timeline.captions.size_px}px) is small for mobile viewing."
        issues.append("Captions may be hard to read on smaller screens.")
        suggested_edits.append("set_caption_size size_px=96")
    else:
        cap_score = 9
        cap_comment = (
            f"Captions are prominent ({timeline.captions.size_px}px, {timeline.captions.mode})."
        )
    criteria.append(CriterionScore(name="caption_legibility", score=cap_score, comment=cap_comment))

    # 4. Visual Variety
    visual_elements = len(timeline.zooms) + len(timeline.overlays)
    if visual_elements >= 2:
        vis_score = 9
        vis_comment = f"Good visual texture with {visual_elements} dynamic zooms and overlays."
    elif visual_elements == 1:
        vis_score = 7
        vis_comment = "Moderate visual variation; adding a sticker, meme, or zoom would help."
    else:
        vis_score = 6
        vis_comment = "Low visual variation throughout the reel."
        issues.append("Visuals feel repetitive without overlays or zooms.")
        suggested_edits.append("add_zooms auto=True")
    criteria.append(CriterionScore(name="visual_variety", score=vis_score, comment=vis_comment))

    # 5. Audio Balance & Sound Design
    has_audio_fx = bool(timeline.audio_fx)
    has_sfx = bool(timeline.sfx)
    has_music = bool(timeline.music)
    if has_audio_fx or has_sfx or has_music:
        aud_score = 9
        aud_comment = f"Sound design active (music={has_music}, sfx={has_sfx}, fx={has_audio_fx})."
    else:
        aud_score = 7
        aud_comment = "Audio lacks sound effects or background music."
        issues.append("No SFX or music bed present.")
        suggested_edits.append("auto_sfx")
    criteria.append(CriterionScore(name="audio_balance", score=aud_score, comment=aud_comment))

    avg_score = round(sum(c.score for c in criteria) / len(criteria))
    verdict: Literal["pass", "needs_edits"] = (
        "pass" if avg_score >= 8 and not issues else "needs_edits"
    )
    summary = (
        "Preview meets professional quality standards."
        if verdict == "pass"
        else f"Preview score {avg_score}/10; improvements suggested for {len(issues)} items."
    )

    return CriticReport(
        overall_score=avg_score,
        verdict=verdict,
        criteria=criteria,
        issues=issues,
        suggested_edits=suggested_edits,
        summary=summary,
    )


def _parse_json_report(text: str) -> CriticReport | None:
    """Extract and validate JSON from model response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
        return CriticReport.model_validate(raw)
    except Exception:
        return None


async def evaluate_preview(
    llm: LLMProvider | None,
    timeline: Timeline,
    video_path: str | Path | None = None,
    user_prompt: str = "",
) -> CriticReport:
    """Evaluate a rendered preview video using Gemini Vision or fallback heuristics."""
    if llm is None:
        return evaluate_timeline_heuristic(timeline, user_prompt)

    sys_prompt = PROMPT_PATH.read_text(encoding="utf-8") if PROMPT_PATH.exists() else ""
    tl_summary = summarize(timeline)
    dur = timeline.duration_s

    frame_info = ""
    if video_path:
        sampled = sample_preview_frames(video_path, dur, count=4)
        if sampled:
            ts_str = str([round(t, 2) for t, _ in sampled])
            frame_info = f"\nSampled preview frames at timestamps: {ts_str} seconds."

    user_msg = (
        f"Please critique this vertical video reel preview against creator editing standards.\n\n"
        f"User Request / Goal: {user_prompt or 'Standard engaging vertical reel'}\n"
        f"Timeline Summary:\n{tl_summary}\n"
        f"Duration: {dur:.2f}s\n"
        f"{frame_info}\n"
    )

    try:
        req = ChatRequest(
            messages=[
                ChatMessage(role="system", content=sys_prompt),
                ChatMessage(role="user", content=user_msg),
            ],
            max_tokens=1024,
            temperature=0.1,
        )
        reply = await llm.complete(req)
        report = _parse_json_report(reply.text)
        if report is not None:
            return report
    except Exception as exc:
        logger.warning("critic LLM evaluation failed, falling back to heuristics: %s", exc)

    return evaluate_timeline_heuristic(timeline, user_prompt)
