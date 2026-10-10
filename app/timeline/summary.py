"""Compact deterministic timeline summary for Director LLM context (~15 lines max)."""

from app.timeline.schema import Timeline


def summarize(tl: Timeline) -> str:
    """Render a token-efficient, key-order-stable summary of the timeline."""
    words = tl.transcript.words
    word_span = f"{words[0].id}..{words[-1].id}" if words else "none"
    n_clips = len(tl.video_clips)
    cuts_per_min = (n_clips - 1) / tl.duration_s * 60 if tl.duration_s > 0 else 0.0
    lines = [
        f"timeline v{tl.version} | {tl.duration_s:.3f}s | {n_clips} clips | "
        f"{cuts_per_min:.1f} cuts/min | words {word_span} | {len(tl.transcript.beats)} beats",
        f"captions {tl.captions.mode} {tl.captions.position} "
        f"wpl={tl.captions.words_per_line} | zooms {len(tl.zooms)} | "
        f"overlays {len(tl.overlays)} | music {'yes' if tl.music else 'no'} | "
        f"sfx {len(tl.sfx)}",
        f"style {tl.style_id or 'none'}",
    ]
    if tl.overlays:
        rendered = ", ".join(
            f"{o.id}({o.type}/{o.layout} @{o.anchor_word}+{o.offset_ms}ms {o.duration_ms}ms)"
            for o in tl.overlays
        )
        lines.append(f"overlays: {rendered}")
    if tl.music:
        suffix = " [attribution required]" if tl.music.attribution_required else ""
        lines.append(f"music: {tl.music.asset}{suffix}")
    return "\n".join(lines)
