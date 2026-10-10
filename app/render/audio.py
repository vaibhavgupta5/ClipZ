"""Audio chain: denoise -> speed -> A/V guard -> loudnorm (two-pass, -14 LUFS)."""

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


def _norm_filter(measured: dict[str, str] | None) -> str:
    base = f"loudnorm=I={_LUFS}:TP=-1.5:LRA=11"
    if measured is None:
        return base + ":print_format=json"
    params = ":".join(f"{k}={v}" for k, v in measured.items())
    suffix = "" if "offset" in measured else ":offset=0"
    return f"{base}:{params}{suffix}:linear=true"


def audio_filter(
    spans: list[tuple[float, float, float]], measured: dict[str, str] | None = None
) -> str:
    """Full audio filter. spans = [(src_in, src_out, speed)]; empty = whole input."""
    tail = f"aresample=async=1,{_norm_filter(measured)}"
    if not spans:
        return f"afftdn,{tail}[aout]"
    parts: list[str] = []
    labels: list[str] = []
    for i, (src_in, src_out, speed) in enumerate(spans):
        tempo = "," + ",".join(atempo_chain(speed)) if abs(speed - 1.0) > 1e-9 else ""
        parts.append(
            f"[0:a]atrim=start={src_in:.3f}:end={src_out:.3f},"
            f"asetpts=PTS-STARTPTS,afftdn{tempo}[ax{i}]"
        )
        labels.append(f"[ax{i}]")
    if len(labels) == 1:
        head = parts[0].rsplit("[ax0]", 1)[0]
        return f"{head},{tail}[aout]"
    return ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1,{tail}[aout]"


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
