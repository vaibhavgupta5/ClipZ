"""Pure timeline ops: each takes a Timeline and returns (new Timeline, Diff). No I/O.

All ranges anchor to word IDs and snap outward to word edges so cuts never
split a word. Inputs are never mutated (frozen models + deep copies).
"""

from app.timeline.errors import OpError
from app.timeline.schema import (
    EPS,
    MAX_SPEED,
    MIN_SPEED,
    AudioEffect,
    CaptionConfig,
    Diff,
    DubTrack,
    MusicBed,
    Overlay,
    SfxHit,
    Timeline,
    VideoClip,
    Voiceover,
    Zoom,
)
from app.timeline.transcript import Word


def _require_word(tl: Timeline, word_id: str) -> Word:
    try:
        return tl.transcript.word_index[word_id]
    except KeyError:
        known = sorted(tl.transcript.word_index)[:12]
        raise OpError(
            "unknown_word",
            f"word {word_id} does not exist in this transcript",
            f"available word IDs include: {', '.join(known)}",
        ) from None


def _expand_to_word_edges(tl: Timeline, lo: float, hi: float) -> tuple[float, float]:
    """Expand [lo, hi] to fully cover every word it strictly overlaps."""
    for w in tl.transcript.words:
        if w.start < hi and w.end > lo:
            lo = min(lo, w.start)
            hi = max(hi, w.end)
    return (max(0.0, lo), hi)


def _cut_clips_at(clips: list[VideoClip], points: list[float]) -> list[VideoClip]:
    """Split clips at source points strictly inside them. Returns new clip list."""
    out: list[VideoClip] = []
    taken = {c.id for c in clips}
    for clip in clips:
        taken.discard(clip.id)  # first piece keeps its own id
        cuts = sorted(p for p in points if clip.src_in + EPS < p < clip.src_out - EPS)
        bounds = [clip.src_in, *cuts, clip.src_out]
        for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False)):
            if b - a < EPS:
                continue
            cid = clip.id if i == 0 else f"{clip.id}b"
            suffix = 2
            while cid in taken:
                cid = f"{clip.id}b{suffix}"
                suffix += 1
            taken.add(cid)
            out.append(VideoClip(id=cid, asset=clip.asset, src_in=a, src_out=b, speed=clip.speed))
    return out


def _unique_id(base: str, taken: set[str]) -> str:
    """``base`` if free, else base~, base~2, base~3, ... (first free)."""
    cid, n = base, 1
    while cid in taken:
        cid = f"{base}~" if n == 1 else f"{base}~{n}"
        n += 1
    taken.add(cid)
    return cid


def _remove_source_range(clips: list[VideoClip], lo: float, hi: float) -> list[VideoClip]:
    """Subtract [lo, hi] from every clip; drop empties. Split pieces get unique IDs."""
    out: list[VideoClip] = []
    taken = {c.id for c in clips}
    for clip in clips:
        if hi <= clip.src_in + EPS or lo >= clip.src_out - EPS:
            out.append(clip)
            continue
        if lo > clip.src_in + EPS:
            out.append(
                VideoClip(
                    id=clip.id, asset=clip.asset, src_in=clip.src_in, src_out=lo, speed=clip.speed
                )
            )
        if hi < clip.src_out - EPS:
            tail_id = clip.id if lo <= clip.src_in + EPS else _unique_id(f"{clip.id}~", taken)
            out.append(
                VideoClip(
                    id=tail_id,
                    asset=clip.asset,
                    src_in=hi,
                    src_out=clip.src_out,
                    speed=clip.speed,
                )
            )
    return [c for c in out if c.src_out - c.src_in >= EPS]


def _replace_clips(tl: Timeline, clips: list[VideoClip]) -> Timeline:
    if not clips:
        raise OpError(
            "empty_timeline",
            "this op would remove every clip",
            "keep at least one word: narrow the word range",
        )
    # model_validate (not model_copy) so an invalid clip list can never be persisted
    return Timeline.model_validate({**tl.model_dump(), "video_clips": clips})


def _contiguous_runs(tl: Timeline, word_ids: list[str]) -> list[tuple[Word, Word]]:
    """Group word IDs into transcript-order contiguous (first, last) runs."""
    order = {w.id: i for i, w in enumerate(tl.transcript.words)}
    for wid in word_ids:
        _require_word(tl, wid)
    ranked = sorted(set(word_ids), key=lambda wid: order[wid])
    runs: list[tuple[Word, Word]] = []
    start = prev = ranked[0]
    for wid in ranked[1:]:
        if order[wid] == order[prev] + 1:
            prev = wid
        else:
            runs.append((tl.transcript.word_index[start], tl.transcript.word_index[prev]))
            start = prev = wid
    runs.append((tl.transcript.word_index[start], tl.transcript.word_index[prev]))
    return runs


def delete_words(tl: Timeline, word_ids: list[str]) -> tuple[Timeline, Diff]:
    """Delete exact word spans (no padding); gaps between runs are removed too."""
    runs = _contiguous_runs(tl, word_ids)
    clips = list(tl.video_clips)
    for first, last in runs:
        clips = _remove_source_range(clips, first.start, last.end)
    new_tl = _replace_clips(tl, clips)
    return new_tl, Diff(op="delete_words", details={"words": ",".join(sorted(set(word_ids)))})


def delete_gaps(tl: Timeline, ranges: list[tuple[float, float]]) -> tuple[Timeline, Diff]:
    """Delete non-speech source ranges (silence). Refuses any range that touches a word."""
    clips = list(tl.video_clips)
    for lo, hi in sorted(ranges):
        if hi - lo < EPS:
            continue
        hit = next((w for w in tl.transcript.words if w.start < hi and w.end > lo), None)
        if hit is not None:
            raise OpError(
                "cuts_word",
                f"gap {lo:.3f}-{hi:.3f}s overlaps word {hit.id}",
                "gaps must lie strictly between words; use delete_range to remove speech",
            )
        clips = _remove_source_range(clips, lo, hi)
    new_tl = _replace_clips(tl, clips)
    return new_tl, Diff(op="delete_gaps", details={"ranges": str(len(ranges))})


def delete_range(
    tl: Timeline, start_word: str, end_word: str, pad_ms: int = 100
) -> tuple[Timeline, Diff]:
    """Delete [start_word.start - pad, end_word.end + pad], expanded to word edges."""
    first = _require_word(tl, start_word)
    last = _require_word(tl, end_word)
    if last.start < first.start:
        raise OpError(
            "reversed_range",
            f"end word {end_word} starts before start word {start_word}",
            "swap them or pick an end word at/after the start word",
        )
    pad = pad_ms / 1000.0
    lo, hi = _expand_to_word_edges(tl, first.start - pad, last.end + pad)
    new_tl = _replace_clips(tl, _remove_source_range(list(tl.video_clips), lo, hi))
    return new_tl, Diff(op="delete_range", details={"range": f"{start_word}..{end_word}"})


def trim(tl: Timeline, start_word: str, end_word: str, pad_ms: int = 100) -> tuple[Timeline, Diff]:
    """Keep only [start_word.start - pad, end_word.end + pad], expanded to word edges."""
    first = _require_word(tl, start_word)
    last = _require_word(tl, end_word)
    if last.start < first.start:
        raise OpError(
            "reversed_range",
            f"end word {end_word} starts before start word {start_word}",
            "swap them or pick an end word at/after the start word",
        )
    pad = pad_ms / 1000.0
    lo, hi = _expand_to_word_edges(tl, first.start - pad, last.end + pad)
    kept: list[VideoClip] = []
    for clip in tl.video_clips:
        a, b = max(clip.src_in, lo), min(clip.src_out, hi)
        if b - a >= EPS:
            kept.append(
                VideoClip(id=clip.id, asset=clip.asset, src_in=a, src_out=b, speed=clip.speed)
            )
    new_tl = _replace_clips(tl, kept)
    return new_tl, Diff(op="trim", details={"range": f"{start_word}..{end_word}"})


def split(tl: Timeline, at_word: str) -> tuple[Timeline, Diff]:
    """Split the clip containing at_word.start at that word's start boundary."""
    word = _require_word(tl, at_word)
    point = word.start
    host = next(
        (c for c in tl.video_clips if c.src_in + EPS < point < c.src_out - EPS),
        None,
    )
    if host is None:
        return tl, Diff(op="split", details={"at": at_word, "noop": "already a boundary"})
    clips = _cut_clips_at(list(tl.video_clips), [point])
    new_tl = _replace_clips(tl, clips)
    return new_tl, Diff(op="split", details={"at": at_word})


def reorder(tl: Timeline, clip_ids: list[str]) -> tuple[Timeline, Diff]:
    """Permute clips into the given ID order (must be an exact permutation)."""
    by_id = {c.id: c for c in tl.video_clips}
    if sorted(clip_ids) != sorted(by_id):
        raise OpError(
            "bad_permutation",
            f"clip order {clip_ids} is not a permutation of timeline clips",
            f"timeline clips are: {sorted(by_id)}",
        )
    new_tl = tl.model_copy(update={"video_clips": [by_id[cid] for cid in clip_ids]}, deep=True)
    return new_tl, Diff(op="reorder", details={"order": ",".join(clip_ids)})


def set_speed(tl: Timeline, start_word: str, end_word: str, factor: float) -> tuple[Timeline, Diff]:
    """Apply a speed factor to the word span (clips are cut at span edges first)."""
    if not MIN_SPEED <= factor <= MAX_SPEED:
        raise OpError(
            "bad_speed",
            f"speed {factor} outside [{MIN_SPEED}, {MAX_SPEED}]",
            f"pick a factor between {MIN_SPEED} and {MAX_SPEED}",
        )
    first = _require_word(tl, start_word)
    last = _require_word(tl, end_word)
    lo, hi = first.start, last.end
    clips = _cut_clips_at(list(tl.video_clips), [lo, hi])
    out = [
        c.model_copy(update={"speed": factor})
        if c.src_in + EPS >= lo and c.src_out - EPS <= hi
        else c
        for c in clips
    ]
    new_tl = _replace_clips(tl, out)
    return new_tl, Diff(
        op="set_speed", details={"range": f"{start_word}..{end_word}", "factor": str(factor)}
    )


def add_overlay(tl: Timeline, overlay: Overlay) -> tuple[Timeline, Diff]:
    """Add an insert anchored to an existing word."""
    _require_word(tl, overlay.anchor_word)
    if any(o.id == overlay.id for o in tl.overlays):
        raise OpError("duplicate_id", f"overlay {overlay.id} already exists", "use a fresh id")
    new_tl = tl.model_copy(update={"overlays": [*tl.overlays, overlay]}, deep=True)
    return new_tl, Diff(op="add_overlay", details={"id": overlay.id})


def remove_overlay(tl: Timeline, overlay_id: str) -> tuple[Timeline, Diff]:
    """Remove an insert by ID."""
    if not any(o.id == overlay_id for o in tl.overlays):
        raise OpError(
            "unknown_overlay",
            f"overlay {overlay_id} does not exist",
            f"existing overlays: {[o.id for o in tl.overlays]}",
        )
    new_tl = tl.model_copy(
        update={"overlays": [o for o in tl.overlays if o.id != overlay_id]}, deep=True
    )
    return new_tl, Diff(op="remove_overlay", details={"id": overlay_id})


def add_zoom(tl: Timeline, zoom: Zoom) -> tuple[Timeline, Diff]:
    """Add a punch-in anchored to an existing word."""
    _require_word(tl, zoom.anchor_word)
    if any(z.id == zoom.id for z in tl.zooms):
        raise OpError("duplicate_id", f"zoom {zoom.id} already exists", "use a fresh id")
    new_tl = tl.model_copy(update={"zooms": [*tl.zooms, zoom]}, deep=True)
    return new_tl, Diff(op="add_zoom", details={"id": zoom.id})


def remove_zoom(tl: Timeline, zoom_id: str) -> tuple[Timeline, Diff]:
    """Remove a zoom by ID."""
    if not any(z.id == zoom_id for z in tl.zooms):
        raise OpError(
            "unknown_zoom",
            f"zoom {zoom_id} does not exist",
            f"existing zooms: {[z.id for z in tl.zooms]}",
        )
    new_tl = tl.model_copy(update={"zooms": [z for z in tl.zooms if z.id != zoom_id]}, deep=True)
    return new_tl, Diff(op="remove_zoom", details={"id": zoom_id})


def set_captions(tl: Timeline, config: CaptionConfig) -> tuple[Timeline, Diff]:
    """Replace the caption config."""
    new_tl = tl.model_copy(update={"captions": config}, deep=True)
    return new_tl, Diff(op="set_captions", details={"mode": config.mode})


def set_audio_fx(tl: Timeline, fx: list[AudioEffect]) -> tuple[Timeline, Diff]:
    """Replace the declarative audio effect chain (denoise/loudnorm/ducking)."""
    new_tl = tl.model_copy(update={"audio_fx": list(fx)}, deep=True)
    return new_tl, Diff(op="set_audio_fx", details={"fx": ",".join(e.type for e in fx)})


def set_reframe(tl: Timeline, mode: str) -> tuple[Timeline, Diff]:
    """Set the 9:16 reframe mode."""
    if mode not in ("center", "face", "blur", "split_bottom", "split", "adaptive"):
        raise OpError(
            "bad_reframe",
            f"reframe mode {mode!r} unknown",
            "pick center, face, blur, split_bottom, split or adaptive",
        )
    new_tl = tl.model_copy(update={"reframe": mode}, deep=True)
    return new_tl, Diff(op="set_reframe", details={"mode": mode})


def set_music(tl: Timeline, bed: MusicBed | None) -> tuple[Timeline, Diff]:
    """Set (or clear, with None) the music bed."""
    new_tl = tl.model_copy(update={"music": bed}, deep=True)
    return new_tl, Diff(op="set_music", details={"asset": bed.asset if bed else "none"})


def add_sfx(tl: Timeline, hit: SfxHit) -> tuple[Timeline, Diff]:
    """Add a sound-effect hit anchored to an existing word."""
    _require_word(tl, hit.anchor_word)
    if any(s.id == hit.id for s in tl.sfx):
        raise OpError("duplicate_id", f"sfx {hit.id} already exists", "use a fresh id")
    new_tl = tl.model_copy(update={"sfx": [*tl.sfx, hit]}, deep=True)
    return new_tl, Diff(op="add_sfx", details={"id": hit.id})


def remove_sfx(tl: Timeline, sfx_id: str) -> tuple[Timeline, Diff]:
    """Remove an SFX hit by ID."""
    if not any(s.id == sfx_id for s in tl.sfx):
        raise OpError(
            "unknown_sfx",
            f"sfx {sfx_id} does not exist",
            f"existing sfx: {[s.id for s in tl.sfx]}",
        )
    new_tl = tl.model_copy(update={"sfx": [s for s in tl.sfx if s.id != sfx_id]}, deep=True)
    return new_tl, Diff(op="remove_sfx", details={"id": sfx_id})


def add_voiceover(tl: Timeline, vo: Voiceover) -> tuple[Timeline, Diff]:
    """Add a narration clip anchored to an existing word."""
    _require_word(tl, vo.anchor_word)
    if any(v.id == vo.id for v in tl.voiceovers):
        raise OpError("duplicate_id", f"voiceover {vo.id} already exists", "use a fresh id")
    new_tl = tl.model_copy(update={"voiceovers": [*tl.voiceovers, vo]}, deep=True)
    return new_tl, Diff(op="add_voiceover", details={"id": vo.id, "asset": vo.asset})


def remove_voiceover(tl: Timeline, vo_id: str) -> tuple[Timeline, Diff]:
    """Remove a narration clip by ID."""
    if not any(v.id == vo_id for v in tl.voiceovers):
        raise OpError(
            "unknown_voiceover",
            f"voiceover {vo_id} does not exist",
            f"existing voiceovers: {[v.id for v in tl.voiceovers]}",
        )
    kept = [v for v in tl.voiceovers if v.id != vo_id]
    return tl.model_copy(update={"voiceovers": kept}, deep=True), Diff(
        op="remove_voiceover", details={"id": vo_id}
    )


def set_dub(tl: Timeline, dub: DubTrack | None) -> tuple[Timeline, Diff]:
    """Set (or clear, with None) the dubbed voice track."""
    new_tl = tl.model_copy(update={"dub": dub}, deep=True)
    return new_tl, Diff(op="set_dub", details={"language": dub.language if dub else "original"})
