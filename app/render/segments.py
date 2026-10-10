"""Timeline segmentation: split by clip boundaries; hash slices for render cache.

Captions and overlays are clipped to each segment window (output seconds) so a
caption/text change only invalidates the segments it touches.
"""

import hashlib
import json

from pydantic import BaseModel, ConfigDict

from app.render.captions_ass import CaptionCue
from app.timeline import snap
from app.timeline.errors import OpError
from app.timeline.schema import Overlay, Timeline, VideoClip, Zoom


class Segment(BaseModel):
    """One renderable slice: single clip + relative-timed extras."""

    model_config = ConfigDict(frozen=True)

    index: int
    start: float  # output seconds
    end: float
    clips: list[VideoClip]
    overlays: list[tuple[Overlay, float, float]]  # (overlay, rel_start, rel_end)
    zooms: list[tuple[Zoom, float]]  # (zoom, rel_at)
    cues: list[CaptionCue]  # relative-timed


def _offsets(tl: Timeline) -> list[float]:
    offsets: list[float] = []
    total = 0.0
    for clip in tl.video_clips:
        offsets.append(total)
        total += clip.output_len
    return offsets


def split_segments(tl: Timeline) -> list[Segment]:
    """One segment per video clip with clipped overlays/cues/zooms."""
    offsets = _offsets(tl)
    words = tl.transcript.words
    segments: list[Segment] = []
    for i, clip in enumerate(tl.video_clips):
        start, end = offsets[i], offsets[i] + clip.output_len

        overlays: list[tuple[Overlay, float, float]] = []
        for overlay in tl.overlays:
            try:
                at, _ = snap.overlay_window(tl, overlay)
            except OpError:
                continue
            o_end = at + overlay.duration_ms / 1000.0
            lo, hi = max(at, start), min(o_end, end)
            if hi - lo > 1e-6:
                overlays.append((overlay, lo - start, hi - start))

        zooms: list[tuple[Zoom, float]] = []
        for zoom in tl.zooms:
            try:
                at = snap.output_time_of(tl, zoom.anchor_word)
            except OpError:
                continue
            if start - 1e-6 <= at <= end + 1e-6:
                zooms.append((zoom, at - start))

        meme_windows = _meme_windows(tl)
        cues: list[CaptionCue] = []
        for w in words:
            try:
                ws = snap.output_time_of(tl, w.id, "start")
                we = snap.output_time_of(tl, w.id, "end")
            except OpError:
                continue  # cut word: no caption
            if any(ms < we and ws < me for ms, me in meme_windows):
                continue  # a meme's own text is the caption while it is on screen
            lo, hi = max(ws, start), min(we, end)
            if hi - lo > 1e-6:
                cues.append(
                    CaptionCue(
                        id=w.id,
                        start=round(lo - start, 3),
                        end=round(hi - start, 3),
                        text=w.display_text,
                    )
                )

        segments.append(
            Segment(
                index=i,
                start=start,
                end=end,
                clips=[clip],
                overlays=overlays,
                zooms=zooms,
                cues=cues,
            )
        )
    return segments


def _meme_windows(tl: Timeline) -> list[tuple[float, float]]:
    """Output-time windows of meme inserts (captions are hidden under them)."""
    out = []
    for o in tl.overlays:
        if o.type != "meme":
            continue
        try:
            at = snap.output_time_of(tl, o.anchor_word, "end") + o.offset_ms / 1000.0
        except OpError:
            continue
        out.append((at, at + o.duration_ms / 1000.0))
    return out


# Bump whenever render logic changes output for the same timeline slice, so cached
# segments rendered by older code are never reused (e.g. the overlay-duration fix).
RENDER_VERSION = "5"


def slice_hash(
    segment: Segment, profile_name: str, ass_text: str, asset_shas: dict[str, str]
) -> str:
    """Content hash: renderer version + slice EDL + profile + captions + overlay bytes."""
    doc = {
        "renderer": RENDER_VERSION,
        "clips": [c.model_dump(mode="json") for c in segment.clips],
        "overlays": [(o.id, o.layout, rs, re) for o, rs, re in segment.overlays],
        "zooms": [(z.id, ra) for z, ra in segment.zooms],
        "profile": profile_name,
        "ass": ass_text,
        "assets": sorted(asset_shas.items()),
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]
