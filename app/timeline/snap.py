"""Word-ID snapping: resolve anchors to output-timeline seconds.

Source coordinates (transcript timings) flow through the ordered cut list and
per-clip speed factors. Cut-away source times have no output mapping.
"""

from app.timeline.errors import OpError
from app.timeline.schema import EPS, Timeline, VideoClip


def _offsets(tl: Timeline) -> list[float]:
    """Output start offset per clip (cumulative lengths)."""
    offsets: list[float] = []
    total = 0.0
    for clip in tl.video_clips:
        offsets.append(total)
        total += clip.output_len
    return offsets


def _locate(tl: Timeline, source_t: float) -> tuple[int, VideoClip] | None:
    """Clip containing source_t (start-inclusive, end-exclusive except timeline end)."""
    clips = tl.video_clips
    for i, clip in enumerate(clips):
        last = i == len(clips) - 1
        if clip.src_in - EPS <= source_t and (
            source_t < clip.src_out - EPS or (last and source_t <= clip.src_out + EPS)
        ):
            return (i, clip)
    return None


def output_time_of(tl: Timeline, word_id: str, where: str = "start") -> float:
    """Output seconds for a word's start (default) or end. Raises if cut away."""
    try:
        word = tl.transcript.word_index[word_id]
    except KeyError:
        raise OpError("unknown_word", f"word {word_id} does not exist", "") from None
    source_t = word.start if where == "start" else word.end
    found = _locate(tl, source_t)
    if found is None:
        raise OpError(
            "cut_word",
            f"word {word_id} was cut from the timeline",
            "pick a word that is still present (see timeline summary)",
        )
    i, clip = found
    return _offsets(tl)[i] + (source_t - clip.src_in) / clip.speed


def _source_to_output(tl: Timeline, source_t: float, side: str) -> float:
    """Map a source time to output, clamping cut-away times to surviving edges."""
    found = _locate(tl, source_t)
    offsets = _offsets(tl)
    if found is not None:
        i, clip = found
        return offsets[i] + (source_t - clip.src_in) / clip.speed
    if side == "lo":
        for i, clip in enumerate(tl.video_clips):
            if clip.src_out > source_t + EPS:
                return offsets[i]  # next surviving clip start
        return tl.duration_s
    for i in range(len(tl.video_clips) - 1, -1, -1):
        clip = tl.video_clips[i]
        if clip.src_in < source_t - EPS:
            return offsets[i] + clip.output_len  # prev surviving clip end
    return 0.0


def output_range_of(
    tl: Timeline, start_word: str, end_word: str, pad_ms: int = 100
) -> tuple[float, float]:
    """Output window covering the padded word span, clamped to surviving media."""
    try:
        first = tl.transcript.word_index[start_word]
        last = tl.transcript.word_index[end_word]
    except KeyError as exc:
        raise OpError("unknown_word", f"word {exc.args[0]} does not exist", "") from None
    pad = pad_ms / 1000.0
    lo = _source_to_output(tl, max(0.0, first.start - pad), "lo")
    hi = _source_to_output(tl, last.end + pad, "hi")
    return (min(lo, hi), max(lo, hi))


def snap_boundary(tl: Timeline, source_t: float, direction: str) -> float:
    """Snap a source time outward to the nearest word edge (never inside a word)."""
    for w in tl.transcript.words:
        if w.start < source_t < w.end:
            return w.start if direction == "backward" else w.end
    return source_t
