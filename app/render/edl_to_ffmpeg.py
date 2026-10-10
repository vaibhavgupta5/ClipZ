"""EDL -> ffmpeg: compile a Timeline slice into inputs + filter script + encoder args."""

import subprocess
from dataclasses import dataclass

from app.render.errors import RenderError
from app.render.graph import (
    AssBurn,
    BlurBg,
    Concat,
    FadeIO,
    FilterGraph,
    OverlayAt,
    ReframeBlur,
    Scale,
    ScaleCrop,
    SetPts,
    Trim,
    ZoomPunch,
)
from app.timeline import snap
from app.timeline.errors import OpError
from app.timeline.schema import Overlay, Timeline, Zoom


@dataclass(frozen=True)
class RenderProfile:
    """Named render target."""

    name: str
    width: int
    height: int
    preset: str
    crf: int


PREVIEW = RenderProfile(name="preview", width=540, height=960, preset="veryfast", crf=28)
FINAL = RenderProfile(name="final", width=1080, height=1920, preset="medium", crf=20)

_nvenc_cached: bool | None = None


def select_video_encoder() -> str:
    """h264_nvenc when a real NVENC encode works, else libx264 (result cached).

    Encoder presence (ffmpeg -h) is NOT enough: the driver may be missing
    (Cannot load libcuda.so.1), so probe with a tiny real encode instead.
    """
    global _nvenc_cached
    if _nvenc_cached is None:
        from app.media.ffmpeg import ffmpeg_path

        try:
            proc = subprocess.run(
                [
                    ffmpeg_path(),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=64x64:rate=5:duration=1",
                    "-c:v",
                    "h264_nvenc",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                check=False,
                timeout=60,
            )
            _nvenc_cached = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _nvenc_cached = False
    return "h264_nvenc" if _nvenc_cached else "libx264"


def encoder_args(profile: RenderProfile, encoder: str) -> list[str]:
    """Codec args for the chosen encoder."""
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "fast", "-cq", str(profile.crf)]
    return ["-c:v", "libx264", "-preset", profile.preset, "-crf", str(profile.crf)]


def build_inputs(master_path: str, image_loops: dict[str, float]) -> list[str]:
    """Master input + looped image inputs (GIF/video overlays land in Phase 7)."""
    args = ["-i", master_path]
    for path, dur in image_loops.items():
        args += ["-loop", "1", "-t", f"{dur:.3f}", "-i", path]
    return args


def _at(tl: Timeline, anchor_word: str, offset_ms: int = 0) -> float:
    try:
        return snap.output_time_of(tl, anchor_word) + offset_ms / 1000.0
    except OpError as exc:
        raise RenderError("dangling_anchor", f"overlay/zoom anchor lost: {exc}") from None


def build_video_filter(
    tl: Timeline,
    profile: RenderProfile,
    asset_labels: dict[str, str] | None = None,
    ass_path: str | None = None,
    reframe: str = "center",
    overlay_windows: list[tuple[Overlay, float, float]] | None = None,
    zoom_windows: list[tuple[Zoom, float]] | None = None,
) -> tuple[str, str]:
    """Compile clips + zooms + overlays + captions to (filter_script, video_out).

    overlay_windows/zoom_windows carry pre-clipped RELATIVE times (segment path);
    when None they are computed from anchors on the full timeline.
    """
    asset_labels = asset_labels or {}
    graph = FilterGraph(inputs=["[0:v]"])
    chains: list[str] = []
    for clip in tl.video_clips:
        label = graph.add(Trim("[0:v]", start=clip.src_in, end=clip.src_out))
        if abs(clip.speed - 1.0) > 1e-9:
            label = graph.add(SetPts(_lbl(label), factor=1.0 / clip.speed))
        if reframe == "blur":
            label = graph.add(ReframeBlur(_lbl(label), profile.width, profile.height))
        else:  # center (face falls back here without tracks)
            label = graph.add(ScaleCrop(_lbl(label), profile.width, profile.height))
        chains.append(_lbl(label))
    main = chains[0] if len(chains) == 1 else graph.add(Concat(tuple(chains), n=len(chains)))

    # Zooms run on the concatenated timeline: expressions use output seconds.
    if zoom_windows is None:
        zoom_windows = [(z, _at(tl, z.anchor_word)) for z in tl.zooms]
    for zoom, at in zoom_windows:
        main = graph.add(
            ZoomPunch(
                _lbl(main),
                at=at,
                dur_s=zoom.dur_ms / 1000.0,
                scale=zoom.scale,
                width=profile.width,
                height=profile.height,
            )
        )

    if overlay_windows is None:
        overlay_windows = []
        for overlay in tl.overlays:
            at = _at(tl, overlay.anchor_word, overlay.offset_ms)
            overlay_windows.append((overlay, at, at + overlay.duration_ms / 1000.0))
    for overlay, at, end in overlay_windows:
        if overlay.asset not in asset_labels:
            raise RenderError("unknown_asset", f"no local file for overlay asset {overlay.asset}")
        over = asset_labels[overlay.asset]
        w, h = profile.width, profile.height
        if overlay.layout == "cutaway_full":
            scaled = graph.add(ScaleCrop(over, w, h))
            main = graph.add(OverlayAt(_lbl(main), _lbl(scaled), 0, 0, at, end))
        elif overlay.layout == "pip_pop":
            pip_w = w // 2
            scaled = graph.add(Scale(over, pip_w, -2))
            main = graph.add(OverlayAt(_lbl(main), _lbl(scaled), w - pip_w - 20, 40, at, end))
        elif overlay.layout == "split_top":
            scaled = graph.add(Scale(over, w, h // 2))
            faded = graph.add(FadeIO(_lbl(scaled), 0.15, 0.15, end - at))
            main = graph.add(OverlayAt(_lbl(main), _lbl(faded), 0, 0, at, end))
        elif overlay.layout == "lower_third_card":
            scaled = graph.add(Scale(over, int(w * 0.85), -2))
            main = graph.add(OverlayAt(_lbl(main), _lbl(scaled), "(W-w)/2", h - 360, at, end))
        else:  # blur_bg_contain
            contained = graph.add(BlurBg(over, w, h))
            main = graph.add(OverlayAt(_lbl(main), _lbl(contained), 0, 0, at, end))

    if ass_path is not None and tl.captions.mode != "off":
        main = graph.add(AssBurn(_lbl(main), ass_path))
    return graph.compile()


def _lbl(label: str) -> str:
    """Ensure bracketed label."""
    return label if label.startswith("[") else f"[{label}]"
