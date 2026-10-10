"""Timeline validators: duration cap, overlaps, anchors, licenses, caption/zoom bounds."""

from app.timeline.errors import TimelineInvalid, ToolError
from app.timeline.schema import EPS, MAX_SPEED, MIN_SPEED, Timeline

MAX_VIDEO_SECONDS = 90


def validate_timeline(
    tl: Timeline,
    *,
    max_seconds: int = MAX_VIDEO_SECONDS,
    known_asset_ids: set[str] | None = None,
) -> list[ToolError]:
    """Collect every violation (empty = valid). Pure function, no I/O."""
    errors: list[ToolError] = []
    duration_ms = round(tl.duration_s * 1000)
    if duration_ms > max_seconds * 1000:
        errors.append(
            ToolError(
                code="too_long",
                field=f"duration={tl.duration_s}s",
                message=f"timeline is {tl.duration_s}s, over the {max_seconds}s cap",
                hint=f"trim or delete at least {duration_ms - max_seconds * 1000}ms",
            )
        )

    # Same-track overlaps (clips are ordered; next must start where prev ended in SOURCE? No:
    # clips are sequential output segments; source ranges must not overlap since each source
    # second maps to exactly one output second).
    spans = sorted((c.src_in, c.src_out, c.id) for c in tl.video_clips)
    for (a0, a1, aid), (b0, _b1, bid) in zip(spans, spans[1:], strict=False):
        if b0 < a1 - EPS:
            errors.append(
                ToolError(
                    code="clip_overlap",
                    field=f"clips={aid},{bid}",
                    message=f"source ranges [{a0},{a1}] and [{b0},...] overlap",
                    hint="reorder or trim so source ranges are disjoint",
                )
            )

    known_words = set(tl.transcript.word_index)
    anchors: list[tuple[str, str]] = [(z.id, z.anchor_word) for z in tl.zooms]
    anchors += [(o.id, o.anchor_word) for o in tl.overlays]
    anchors += [(s.id, s.anchor_word) for s in tl.sfx]
    anchors += [(v.id, v.anchor_word) for v in tl.voiceovers]
    for owner, wid in anchors:
        if wid not in known_words:
            errors.append(
                ToolError(
                    code="dangling_anchor",
                    field=f"{owner}.anchor_word={wid}",
                    message=f"{owner} anchors to missing word {wid}",
                    hint="anchor to a word ID from the transcript (see timeline summary)",
                )
            )

    licensed = [(o.id, o.license) for o in tl.overlays]
    if tl.music is not None:
        licensed.append(("music", tl.music.license))
    licensed += [(s.id, s.license) for s in tl.sfx]
    licensed += [(v.id, v.license) for v in tl.voiceovers]
    if tl.dub is not None:
        licensed.append(("dub", tl.dub.license))
    for owner, lic in licensed:
        if not lic.strip():
            errors.append(
                ToolError(
                    code="missing_license",
                    field=f"{owner}.license",
                    message=f"{owner} has no license metadata",
                    hint="no asset without license metadata reaches a render (rule 7)",
                )
            )

    wpl = tl.captions.words_per_line
    if not 1 <= wpl <= 4:
        errors.append(
            ToolError(
                code="bad_caption_layout",
                field=f"captions.words_per_line={wpl}",
                message=f"words_per_line={wpl} outside 1..4",
                hint="pick 1..3 for reel styles (4 max for dense explainers)",
            )
        )

    for z in tl.zooms:
        if not 1.0 <= z.scale <= 2.0:
            errors.append(
                ToolError(
                    code="bad_zoom",
                    field=f"{z.id}.scale={z.scale}",
                    message=f"zoom scale {z.scale} outside 1.0..2.0",
                    hint="pick a scale between 1.0 and 2.0",
                )
            )

    for clip in tl.video_clips:
        if not MIN_SPEED <= clip.speed <= MAX_SPEED:
            errors.append(
                ToolError(
                    code="bad_speed",
                    field=f"{clip.id}.speed={clip.speed}",
                    message=f"speed {clip.speed} outside [{MIN_SPEED}, {MAX_SPEED}]",
                    hint=f"pick a factor between {MIN_SPEED} and {MAX_SPEED}",
                )
            )

    if known_asset_ids is not None:
        for clip in tl.video_clips:
            if clip.asset not in known_asset_ids:
                errors.append(
                    ToolError(
                        code="unknown_asset",
                        field=f"{clip.id}.asset={clip.asset}",
                        message=f"clip {clip.id} references unknown asset {clip.asset}",
                        hint="use an asset ID from the project",
                    )
                )
    return errors


def ensure_valid(
    tl: Timeline, *, max_seconds: int = MAX_VIDEO_SECONDS, known_asset_ids: set[str] | None = None
) -> None:
    """Raise TimelineInvalid when the timeline violates any rule."""
    errors = validate_timeline(tl, max_seconds=max_seconds, known_asset_ids=known_asset_ids)
    if errors:
        raise TimelineInvalid(errors)
