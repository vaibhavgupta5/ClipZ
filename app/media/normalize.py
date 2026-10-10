"""Deterministic ffmpeg arg builders (pure: no subprocess here).

Conventions: `-noautorotate` + manual transpose (rotation applied exactly once),
`fps=30` filter for CFR (not `-r`), SDR tone-map only for HDR transfers.
"""

from app.media.ffmpeg import ffmpeg_path
from app.media.models import MediaInfo

_TRANSPOSE = {90: ["transpose=1"], 180: ["transpose=1", "transpose=1"], 270: ["transpose=2"]}


def _video_chain(info: MediaInfo, for_proxy: bool = False) -> str:
    parts: list[str] = []
    if info.rotation_deg in _TRANSPOSE:
        parts.extend(_TRANSPOSE[info.rotation_deg])
    if for_proxy:
        parts.append("scale=540:-2")
    parts.append("fps=30")
    if info.is_hdr:
        parts.append("tonemap=hable:desat=0")
        parts.append("colorspace=all=bt709:iall=bt2020nc:fast=1")
    parts.append("format=yuv420p")
    return ",".join(parts)


def normalize_args(src: str, dst: str, info: MediaInfo) -> list[str]:
    """Master: CFR 30, SDR, rotation applied, 48 kHz stereo, quality CRF."""
    args = [
        ffmpeg_path(),
        "-y",
        "-protocol_whitelist",
        "file",
        "-noautorotate",
        "-i",
        src,
        "-map",
        "0:v:0",
    ]
    if info.acodec is not None:
        args += ["-map", "0:a:0?"]
    vf = _video_chain(info)
    args += ["-vf", vf, "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "medium", "-crf", "18"]
    args += ["-metadata:s:v:0", "rotate=0"]  # rotation baked into pixels; clear the tag
    if info.acodec is not None:
        args += ["-af", "aresample=48000,aformat=channel_layouts=stereo"]
        args += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
    else:
        args += ["-an"]
    return [*args, dst]


def proxy_args(src: str, dst: str) -> list[str]:
    """Preview: 540px wide, 0.5 s GOP (keyint 15 @30fps), faststart."""
    info_hint = MediaInfo(
        duration_s=1.0,
        width=720,
        height=1280,
        fps=30.0,
        avg_fps=30.0,
        vcodec="h264",
        acodec="aac",
        audio_channels=2,
        sample_rate=48000,
        size_bytes=1,
    )
    return [
        ffmpeg_path(),
        "-y",
        "-protocol_whitelist",
        "file",
        "-i",
        src,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        _video_chain(info_hint, for_proxy=True),
        "-fps_mode",
        "cfr",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-g",
        "15",
        "-keyint_min",
        "15",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        dst,
    ]


def asr_audio_args(src: str, dst: str) -> list[str]:
    """ASR feed: mono 16 kHz PCM."""
    return [
        ffmpeg_path(),
        "-y",
        "-protocol_whitelist",
        "file",
        "-i",
        src,
        "-map",
        "0:a:0",
        "-vn",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        dst,
    ]


def silent_asr_args(duration_s: float, dst: str) -> list[str]:
    """Silent ASR feed (for silent uploads): mono 16 kHz digital silence."""
    return [
        ffmpeg_path(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=16000:cl=mono:d={duration_s:.3f}",
        "-c:a",
        "pcm_s16le",
        dst,
    ]
