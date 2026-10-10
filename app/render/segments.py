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
    is_speech_cut: bool = False
    is_split_layout: bool = False


def _offsets(tl: Timeline) -> list[float]:
    offsets: list[float] = []
    total = 0.0
    for clip in tl.video_clips:
        offsets.append(total)
        total += clip.output_len
    return offsets


def _adaptive_split_intervals(tl: Timeline, min_hold_s: float = 6.0) -> list[tuple[float, float]]:
    """Compute smoothed [start, end] intervals for split-screen layout in adaptive mode.

    Ensures that:
    1. Nearby split overlays (gap < min_hold_s) are bridged into a continuous split section,
       preventing jarring micro-switches/strobe between split and middle blur.
    2. Split-screen sections last at least min_hold_s (or up to total duration).
    3. Middle blur sections (gaps between split sections or at boundaries) last at least min_hold_s;
       micro-gaps (< min_hold_s) are merged into adjacent split sections.
    """
    total_dur = tl.duration_s
    hold = min(min_hold_s, total_dur * 0.4) if total_dur > 0 else min_hold_s

    raw_intervals: list[tuple[float, float]] = []
    for overlay in tl.overlays:
        if overlay.layout == "split_top":
            try:
                at, _ = snap.overlay_window(tl, overlay)
            except OpError:
                continue
            o_end = at + overlay.duration_ms / 1000.0
            raw_intervals.append((max(0.0, at), min(total_dur, o_end)))

    if not raw_intervals:
        return []

    raw_intervals.sort(key=lambda x: x[0])

    # 1. Merge overlapping or closely spaced intervals (gap < hold)
    merged: list[list[float]] = []
    for s, e in raw_intervals:
        target_e = min(total_dur, max(e, s + hold))
        if not merged:
            merged.append([s, target_e])
        else:
            prev_s, prev_e = merged[-1]
            if s - prev_e < hold:
                merged[-1][1] = max(prev_e, target_e)
            else:
                merged.append([s, target_e])

    # 2. Smooth boundaries (intro & outro)
    if merged and merged[0][0] < hold:
        merged[0][0] = 0.0

    if merged and (total_dur - merged[-1][1]) < hold:
        merged[-1][1] = total_dur

    # 3. Check internal gaps between merged segments: if any gap < hold, merge them
    final_intervals: list[tuple[float, float]] = []
    for s, e in merged:
        if not final_intervals:
            final_intervals.append((s, e))
        else:
            prev_s, prev_e = final_intervals[-1]
            if s - prev_e < hold:
                final_intervals[-1] = (prev_s, max(prev_e, e))
            else:
                final_intervals.append((s, e))

    return final_intervals


def _chained_split_overlay_windows(
    tl: Timeline, split_intervals: list[tuple[float, float]]
) -> dict[str, tuple[float, float]]:
    """Compute contiguous (start, end) time windows for split_top overlays so the top
    panel in split view is NEVER empty or black at any moment."""
    raw_split_overlays: list[tuple[Overlay, float, float]] = []
    for overlay in tl.overlays:
        if overlay.layout == "split_top":
            try:
                at, _ = snap.overlay_window(tl, overlay)
            except OpError:
                continue
            o_end = at + overlay.duration_ms / 1000.0
            raw_split_overlays.append((overlay, at, o_end))

    if not raw_split_overlays:
        return {}

    raw_split_overlays.sort(key=lambda x: x[1])
    windows: dict[str, tuple[float, float]] = {}

    for s_int, e_int in split_intervals:
        # Overlays belonging to this interval
        interval_overlays = [
            (ov, at, o_end)
            for ov, at, o_end in raw_split_overlays
            if not (o_end <= s_int or at >= e_int)
        ]
        if not interval_overlays:
            continue

        for idx, (ov, at, o_end) in enumerate(interval_overlays):
            # First overlay starts at interval boundary so top is never empty at start
            cur_start = s_int if idx == 0 else max(s_int, at)
            # If not last overlay, extend until next overlay starts
            if idx < len(interval_overlays) - 1:
                next_at = interval_overlays[idx + 1][1]
                cur_end = min(e_int, max(cur_start + 0.1, next_at))
            else:
                # Last overlay extends to the end of the split interval
                cur_end = e_int

            if cur_end > cur_start:
                windows[ov.id] = (cur_start, cur_end)

    return windows


def split_segments(tl: Timeline) -> list[Segment]:
    """One segment per video clip with clipped overlays/cues/zooms.

    When reframe is adaptive, clips are further sliced at smoothed split-screen
    boundaries so the visual layout dynamically settles for >= 6-7s between split
    and blurred middle view without rapid flickering, and top panel is always filled.
    """
    offsets = _offsets(tl)
    words = tl.transcript.words
    segments: list[Segment] = []
    seg_idx = 0

    split_intervals: list[tuple[float, float]] = []
    if tl.reframe in ("adaptive", "split_adaptive"):
        split_intervals = _adaptive_split_intervals(tl)
    elif tl.reframe in ("split", "split_bottom"):
        split_intervals = [(0.0, tl.duration_s)]

    chained_windows = _chained_split_overlay_windows(tl, split_intervals)

    for i, clip in enumerate(tl.video_clips):
        clip_start, clip_end = offsets[i], offsets[i] + clip.output_len

        # Determine split points inside this clip
        split_points = {clip_start, clip_end}
        if tl.reframe in ("adaptive", "split_adaptive"):
            for s_int, e_int in split_intervals:
                if clip_start < s_int < clip_end:
                    split_points.add(s_int)
                if clip_start < e_int < clip_end:
                    split_points.add(e_int)

        sorted_points = sorted(split_points)
        for p_idx in range(len(sorted_points) - 1):
            start = sorted_points[p_idx]
            end = sorted_points[p_idx + 1]
            if end - start < 1e-4:
                continue

            # Compute slice subclip
            rel_in = (start - clip_start) * clip.speed
            rel_out = (end - clip_start) * clip.speed
            sub_clip = VideoClip(
                id=f"{clip.id}_s{p_idx}" if len(sorted_points) > 2 else clip.id,
                asset=clip.asset,
                src_in=clip.src_in + rel_in,
                src_out=clip.src_in + rel_out,
                speed=clip.speed,
            )

            overlays: list[tuple[Overlay, float, float]] = []
            for overlay in tl.overlays:
                if overlay.id in chained_windows:
                    at, o_end = chained_windows[overlay.id]
                else:
                    try:
                        at, _ = snap.overlay_window(tl, overlay)
                    except OpError:
                        continue
                    o_end = at + overlay.duration_ms / 1000.0
                lo, hi = max(at, start), min(o_end, end)
                if hi - lo > 1e-4:
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

            is_first_slice_of_clip = p_idx == 0
            mid_time = (start + end) / 2.0
            is_split_layout = any(s_int <= mid_time <= e_int for s_int, e_int in split_intervals)
            segments.append(
                Segment(
                    index=seg_idx,
                    start=start,
                    end=end,
                    clips=[sub_clip],
                    overlays=overlays,
                    zooms=zooms,
                    cues=cues,
                    is_speech_cut=is_first_slice_of_clip and (i > 0),
                    is_split_layout=is_split_layout,
                )
            )
            seg_idx += 1

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
RENDER_VERSION = "8"


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
