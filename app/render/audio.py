"""Audio chain: [denoise] -> speed -> A/V guard -> loudnorm (two-pass, default -14 LUFS)."""

import json
import re

_LUFS = -14.0


def atempo_chain(speed: float) -> list[str]:
    """Decompose a speed factor into atempo stages (each 0.5..2.0)."""
    if abs(speed - 1.0) < 1e-9:
        return []
    stages: list[float] = []
    rest = speed
    while rest > 2.0 + 1e-9:
        stages.append(2.0)
        rest /= 2.0
    while rest < 0.5 - 1e-9:
        stages.append(0.5)
        rest /= 0.5
    stages.append(round(rest, 3))
    return [f"atempo={s:.3f}" for s in stages]


def _norm_filter(measured: dict[str, str] | None, lufs: float = _LUFS) -> str:
    if not -24.0 <= lufs <= -8.0:
        raise ValueError(f"loudness target {lufs} outside -24..-8 LUFS")
    base = f"loudnorm=I={lufs:.1f}:TP=-1.5:LRA=11"
    if measured is None:
        return base + ":print_format=json"
    params = ":".join(f"{k}={v}" for k, v in measured.items())
    suffix = "" if "offset" in measured else ":offset=0"
    return f"{base}:{params}{suffix}:linear=true"


def music_chain(level_db: float, duration_s: float, fade_s: float = 1.5) -> str:
    """Bed on input 1, sidechain-ducked under [sc]; returns the chain ending in [duck]."""
    if not -40.0 <= level_db <= 0.0:
        raise ValueError(f"music level {level_db} dB outside -40..0")
    fade_at = max(0.0, duration_s - fade_s)
    return (
        f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={level_db:.1f}dB,"
        f"atrim=end={duration_s:.3f},afade=t=in:d=0.4,afade=t=out:st={fade_at:.3f}:d={fade_s:.2f}[mus];"
        "[mus][sc]sidechaincompress=threshold=0.04:ratio=6:attack=15:release=350:makeup=1[duck]"
    )


def audio_filter(
    spans: list[tuple[float, float, float]],
    measured: dict[str, str] | None = None,
    *,
    denoise: bool = True,
    lufs: float = _LUFS,
    music: tuple[float, float] | None = None,
    sfx: list[tuple[float, float, float]] | None = None,
) -> str:
    """Full audio filter. spans = [(src_in, src_out, speed)]; empty = whole input.

    Inputs: 0 = voice, 1 = music bed (when ``music``), then one input per SFX hit.
    music = (level_db, output_duration_s): looped by the caller, ducked under the voice.
    sfx = [(at_s, gain_db, max_s)]: each hit trimmed (short fade) and delayed to its time.
    Everything is mixed first and loudness-normalised together.
    """
    tail = f"aresample=async=1,{_norm_filter(measured, lufs)}"
    parts = [f"{_voice_filter(spans, denoise)}[v0]"]
    bed = "[v0]"
    if music is not None:
        level_db, duration_s = music
        parts.append("[v0]asplit=2[vo][sc]")
        parts.append(music_chain(level_db, duration_s))
        parts.append("[vo][duck]amix=inputs=2:duration=first:normalize=0[bed]")
        bed = "[bed]"
    hits = sfx or []
    first_sfx = 2 if music is not None else 1
    labels = []
    for k, (at_s, gain_db, max_s) in enumerate(hits):
        if not -40.0 <= gain_db <= 6.0:
            raise ValueError(f"sfx gain {gain_db} dB outside -40..6")
        ms = max(0, round(at_s * 1000))
        parts.append(
            f"[{first_sfx + k}:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"atrim=end={max_s:.3f},afade=t=out:st={max(0.0, max_s - 0.08):.3f}:d=0.08,"
            f"volume={gain_db:.1f}dB,adelay={ms}|{ms}[s{k}]"
        )
        labels.append(f"[s{k}]")
    if labels:
        mix = f"{bed}{''.join(labels)}amix=inputs={len(labels) + 1}:duration=first:normalize=0,"
        parts.append(f"{mix}{tail}[aout]")
    else:
        parts.append(f"{bed}{tail}[aout]")
    return ";".join(parts)


def _voice_filter(spans: list[tuple[float, float, float]], denoise: bool) -> str:
    """Cut/denoised/sped voice track (no output label: callers append the next filter)."""
    dn = ",afftdn" if denoise else ""
    if not spans:
        return f"[0:a]anull{dn}"
    parts: list[str] = []
    labels: list[str] = []
    for i, (src_in, src_out, speed) in enumerate(spans):
        tempo = "," + ",".join(atempo_chain(speed)) if abs(speed - 1.0) > 1e-9 else ""
        parts.append(
            f"[0:a]atrim=start={src_in:.3f}:end={src_out:.3f},"
            f"asetpts=PTS-STARTPTS{dn}{tempo}[ax{i}]"
        )
        labels.append(f"[ax{i}]")
    if len(labels) == 1:
        return parts[0].rsplit("[ax0]", 1)[0]
    return ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1"


def loudnorm_args(pass_n: int, measured: dict[str, str] | None = None) -> list[str]:
    """Args for a loudnorm measurement (1) or linear correction (2) pass."""
    if pass_n == 1:
        return ["-af", _norm_filter(None)]
    if measured is None:
        raise ValueError("pass 2 needs measured values from pass 1")
    return ["-af", _norm_filter(measured)]


def parse_loudnorm_json(stderr: str) -> dict[str, str]:
    """Extract the loudnorm JSON block from ffmpeg stderr."""
    matches = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr)
    if not matches:
        raise ValueError("no loudnorm JSON in ffmpeg output")
    return dict(json.loads(matches[-1]))


def measure_loudness(path: str) -> dict[str, str]:
    """Single-pass loudnorm measurement of a rendered file."""
    from app.media.ffmpeg import ffmpeg_path, run_get_stderr

    stderr = run_get_stderr(
        [ffmpeg_path(), "-hide_banner", "-i", path, *loudnorm_args(pass_n=1), "-f", "null", "-"]
    )
    return parse_loudnorm_json(stderr)
