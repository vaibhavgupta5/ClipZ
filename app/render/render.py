"""Segment-parallel renderer: hash-cached slices, concat, full audio pass, mux."""

import asyncio
import collections
import hashlib
import json
import re
import subprocess
import threading
import unicodedata
from collections.abc import Callable
from pathlib import Path

from app.media.ffmpeg import ffmpeg_path, run_ffmpeg, run_get_stderr
from app.render.audio import audio_filter, parse_loudnorm_json
from app.render.captions_ass import ass_for_cues
from app.render.edl_to_ffmpeg import (
    FINAL,  # noqa: F401  (re-exported for workers)
    PREVIEW,  # noqa: F401  (re-exported for workers)
    RenderProfile,
    build_inputs,
    build_video_filter,
    encoder_args,
    select_video_encoder,
)
from app.render.errors import RenderError
from app.render.segments import Segment, slice_hash, split_segments
from app.storage import Storage
from app.timeline import snap
from app.timeline.schema import Timeline

_PROFILE_BY_NAME = {"preview": PREVIEW, "final": FINAL}


def profile_for(name: str) -> RenderProfile:
    """Named profile or RenderError."""
    try:
        return _PROFILE_BY_NAME[name]
    except KeyError:
        raise RenderError("bad_profile", f"unknown profile {name!r} (want preview|final)") from None


def _run_with_progress(
    args: list[str],
    duration_s: float,
    on_progress: Callable[[float], None],
    base: float,
    span: float,
    timeout_s: int = 600,
) -> None:
    """Run ffmpeg with -progress pipe; report base + span * frac. Last arg must be the output."""
    if not args:
        raise RenderError("bad_args", "empty ffmpeg arg list")
    full = [*args[:-1], "-progress", "pipe:1", "-nostats", args[-1]]
    proc = subprocess.Popen(
        full, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    # Drain stderr concurrently: if only stdout is read, a chatty ffmpeg (libass logs one
    # line per caption event) fills the stderr pipe and both processes block forever.
    tail: collections.deque[str] = collections.deque(maxlen=200)
    assert proc.stderr is not None and proc.stdout is not None
    drain = threading.Thread(target=lambda: tail.extend(proc.stderr), daemon=True)  # type: ignore[arg-type]
    drain.start()
    killer = threading.Timer(timeout_s, proc.kill)
    killer.start()
    # ffmpeg reports out_time_ms in MICROseconds (historical naming quirk); prefer out_time_us.
    out_time = re.compile(r"out_time_(?:us|ms)=(\d+)")
    try:
        for line in proc.stdout:
            match = out_time.match(line.strip())
            if match and duration_s > 0:
                frac = min(1.0, int(match.group(1)) / 1_000_000 / duration_s)
                on_progress(base + span * frac)
        proc.wait()
    finally:
        timed_out = not killer.is_alive()
        killer.cancel()
        drain.join(timeout=5)
    if timed_out and proc.returncode != 0:
        raise RenderError("ffmpeg_timeout", f"ffmpeg render timed out after {timeout_s}s")
    if proc.returncode != 0:
        raise RenderError(
            "ffmpeg_failed", f"ffmpeg exit {proc.returncode}: {''.join(tail)[-2000:]}"
        )


def _timeline_hash(tl: Timeline, profile: RenderProfile) -> str:
    doc = {"edl": tl.model_dump(mode="json"), "profile": profile.name}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:16]


def _sha_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _needs_devanagari(seg: Segment) -> bool:
    return any("DEVANAGARI" in unicodedata.name(ch, "") for cue in seg.cues for ch in cue.text)


async def render_timeline(
    tl: Timeline,
    master_path: str,
    asset_files: dict[str, str],
    profile: RenderProfile,
    store: Storage,
    workdir: str,
    on_progress: Callable[[float], None],
) -> dict[str, object]:
    """Render a timeline to object storage. Returns {key, stats, timeline_hash}."""
    work = Path(workdir)
    segments = split_segments(tl)
    total_dur = tl.duration_s or 0.001
    encoder = await asyncio.to_thread(select_video_encoder)
    stats = {"hits": 0, "misses": 0}
    seg_paths: list[str] = []

    for seg in segments:
        seg_reframe = tl.reframe
        if tl.reframe in ("adaptive", "split_adaptive"):
            seg_reframe = "split_bottom" if seg.is_split_layout else "blur"

        # Gaussian blur view (middle video with blur bars) must shift subtitles to
        # bottom ("center_lower") instead of middle ("center") to avoid covering the speaker.
        caption_pos = "center_lower" if seg_reframe == "blur" else tl.captions.position

        ass_text = ass_for_cues(
            seg.cues,
            font="devanagari" if _needs_devanagari(seg) else "latin",
            position=caption_pos,
            words_per_line=tl.captions.words_per_line,
            mode=tl.captions.mode,
            size_px=tl.captions.size_px,
            base_color=tl.captions.base_color,
            highlight_color=tl.captions.highlight_color,
            uppercase=tl.captions.uppercase,
            animation=tl.captions.animation,
        )
        asset_shas = {aid: _sha_file(asset_files[aid]) for aid in _seg_asset_ids(seg, asset_files)}
        cut_fx = tl.transition if seg.is_speech_cut else "none"
        variant = f"{profile.name}:{seg_reframe}:{cut_fx}"  # these change every pixel
        key = f"segments/{slice_hash(seg, variant, ass_text, asset_shas)}.mp4"
        local = work / f"seg{seg.index}.mp4"
        try:
            store.stat(key)
            local.write_bytes(store.get_bytes(key))
            stats["hits"] += 1
        except KeyError:
            stats["misses"] += 1
            await asyncio.to_thread(
                _render_segment,
                tl,
                seg,
                master_path,
                asset_files,
                ass_text,
                profile,
                encoder,
                str(local),
                on_progress,
                seg.start / total_dur,
                (seg.end - seg.start) / total_dur,
                seg.is_speech_cut,
            )
            store.put_bytes(key, local.read_bytes(), "video/mp4")
        seg_paths.append(str(local))

    concat_list = work / "concat.txt"
    concat_list.write_text("".join(f"file '{p}'\n" for p in seg_paths))
    video_only = work / "video.mp4"
    await asyncio.to_thread(
        run_ffmpeg,
        [
            ffmpeg_path(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(video_only),
        ],
    )
    on_progress(0.85)

    music_path = asset_files.get(tl.music.asset) if tl.music else None
    audio_path = await asyncio.to_thread(
        _render_audio, tl, master_path, work, music_path, asset_files
    )
    on_progress(0.95)

    out_hash = _timeline_hash(tl, profile)
    out_key = f"renders/{tl.project_id}/{profile.name}-{out_hash}.mp4"
    out_local = work / "out.mp4"
    await asyncio.to_thread(
        run_ffmpeg,
        [
            ffmpeg_path(),
            "-y",
            "-i",
            str(video_only),
            "-i",
            str(audio_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(out_local),
        ],
    )
    store.put_bytes(out_key, out_local.read_bytes(), "video/mp4")
    on_progress(1.0)
    return {"key": out_key, "stats": stats, "timeline_hash": out_hash}


def _seg_asset_ids(seg: Segment, asset_files: dict[str, str]) -> list[str]:
    ids: list[str] = []
    for overlay, _, _ in seg.overlays:
        if overlay.asset not in asset_files:
            raise RenderError("unknown_asset", f"no local file for overlay asset {overlay.asset}")
        if overlay.asset not in ids:
            ids.append(overlay.asset)
    return ids


def _render_segment(
    tl: Timeline,
    seg: Segment,
    master: str,
    asset_files: dict[str, str],
    ass_text: str,
    profile: RenderProfile,
    encoder: str,
    out_path: str,
    on_progress: Callable[[float], None],
    base: float,
    span: float,
    styled_cut: bool = False,
) -> None:
    ass_path = out_path + ".ass"
    Path(ass_path).write_text(ass_text, encoding="utf-8")
    for overlay, _, _ in seg.overlays:
        if overlay.asset not in asset_files:
            raise RenderError("unknown_asset", f"no local file for overlay asset {overlay.asset}")
    # One input per overlay window (same asset twice = two inputs, each time-shifted).
    # Each overlay input lasts exactly its window: ShiftPts moves it to `start`, so a
    # longer input would outlive the segment and stretch the video stream.
    inputs = build_inputs(
        master, [(asset_files[o.asset], end - start) for o, start, end in seg.overlays]
    )
    asset_labels = {o.id: f"[{i}:v]" for i, (o, _, _) in enumerate(seg.overlays, start=1)}
    slice_tl = tl.model_copy(update={"video_clips": seg.clips})
    script, video_out = build_video_filter(
        slice_tl,
        profile,
        asset_labels,
        ass_path=ass_path if tl.captions.mode != "off" else None,
        overlay_windows=seg.overlays,
        zoom_windows=seg.zooms,
        after_cut=styled_cut,
    )
    args = (
        [ffmpeg_path(), "-y"]
        + inputs
        + ["-filter_complex", script, "-map", video_out, "-an"]
        + encoder_args(profile, encoder)
        + ["-threads", "1", out_path]
    )
    _run_with_progress(args, seg.end - seg.start, on_progress, base, span)


def audio_settings(tl: Timeline) -> tuple[bool, float]:
    """(denoise, lufs) from the timeline's declarative audio_fx; loudnorm defaults to -14."""
    denoise = any(e.type == "denoise" for e in tl.audio_fx)
    lufs = next((e.params.get("lufs", -14.0) for e in tl.audio_fx if e.type == "loudnorm"), -14.0)
    return denoise, lufs


def sfx_hits(tl: Timeline, files: dict[str, str]) -> list[tuple[str, float, float, float]]:
    """(file, output_seconds, gain_db, max_s) for each hit whose anchor survives the cuts."""
    from app.timeline import snap
    from app.timeline.errors import OpError

    out = []
    for hit in tl.sfx:
        path = files.get(hit.asset)
        if path is None:
            raise RenderError("unknown_asset", f"no local file for sfx {hit.asset}")
        try:
            at = snap.output_time_of(tl, hit.anchor_word, "end") + hit.offset_ms / 1000.0
        except OpError:
            continue  # its word was cut: drop the hit
        if 0.0 <= at < tl.duration_s:
            out.append((path, at, hit.gain_db, hit.max_ms / 1000.0))
    return out


def voiceover_hits(
    tl: Timeline, files: dict[str, str]
) -> tuple[list[tuple[str, float, float, float]], list[tuple[float, float, float]]]:
    """Voiceovers as mix hits (file, at_s, gain_db, len_s) plus voice duck windows."""
    from app.timeline import snap
    from app.timeline.errors import OpError

    hits, ducks = [], []
    for vo in tl.voiceovers:
        path = files.get(vo.asset)
        if path is None:
            raise RenderError("unknown_asset", f"no local file for voiceover {vo.asset}")
        try:
            at = snap.output_time_of(tl, vo.anchor_word, "end") + vo.offset_ms / 1000.0
        except OpError:
            continue  # its word was cut: drop the narration
        if not 0.0 <= at < tl.duration_s:
            continue
        length = vo.duration_ms / 1000.0
        hits.append((path, at, vo.gain_db, length))
        ducks.append((max(0.0, at - 0.1), min(tl.duration_s, at + length + 0.1), vo.duck_db))
    return hits, ducks


def _render_audio(
    tl: Timeline,
    master: str,
    work: Path,
    music_path: str | None = None,
    sfx_files: dict[str, str] | None = None,
) -> str:
    from app.media.probe import probe_file

    denoise, lufs = audio_settings(tl)
    music = (tl.music.level_db, tl.duration_s) if tl.music and music_path else None
    music_in = ["-stream_loop", "-1", "-i", music_path] if music and music_path else []
    hits = sfx_hits(tl, sfx_files or {})
    vo_hits, ducks = voiceover_hits(tl, sfx_files or {})
    hits += vo_hits
    sfx_in = [arg for path, _, _, _ in hits for arg in ("-i", path)]
    sfx = [(at, gain, max_s) for _, at, gain, max_s in hits]
    try:
        has_audio = probe_file(master).acodec is not None
    except Exception:
        has_audio = False
    voice_src = master
    if tl.dub is not None:
        dub_file = (sfx_files or {}).get(tl.dub.asset)
        if dub_file is None:
            raise RenderError("unknown_asset", f"no local file for dub {tl.dub.asset}")
        voice_src, has_audio = dub_file, True  # same timing as the master's audio
    if has_audio:
        audio_in: list[str] = ["-i", voice_src]
        spans = [(c.src_in, c.src_out, c.speed) for c in tl.video_clips]
    else:
        # Silence is generated at OUTPUT length; spans use output coordinates.
        audio_in = ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={tl.duration_s:.3f}"]
        spans = []
        offset = 0.0
        for c in tl.video_clips:
            out_len = (c.src_out - c.src_in) / c.speed
            spans.append((offset, offset + out_len, c.speed))
            offset += out_len
    stderr = run_get_stderr(
        [
            ffmpeg_path(),
            "-hide_banner",
            *audio_in,
            *music_in,
            *sfx_in,
            "-filter_complex",
            audio_filter(spans, denoise=denoise, lufs=lufs, music=music, sfx=sfx, ducks=ducks),
            "-map",
            "[aout]",
            "-f",
            "null",
            "-",
        ]
    )
    measured_full = parse_loudnorm_json(stderr)
    measured = {
        "measured_I": measured_full["input_i"],
        "measured_TP": measured_full["input_tp"],
        "measured_LRA": measured_full["input_lra"],
        "measured_thresh": measured_full["input_thresh"],
        "offset": measured_full.get("target_offset", "0"),
    }
    out = work / "audio.m4a"
    run_ffmpeg(
        [
            ffmpeg_path(),
            "-y",
            *audio_in,
            *music_in,
            *sfx_in,
            "-filter_complex",
            audio_filter(
                spans, measured, denoise=denoise, lufs=lufs, music=music, sfx=sfx, ducks=ducks
            ),
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(out),
        ]
    )
    return str(out)
