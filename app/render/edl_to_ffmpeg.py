"""EDL -> ffmpeg: compile a Timeline slice into inputs + filter script + encoder args."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.render.errors import RenderError
from app.render.framing import Focus
from app.render.graph import (
    AssBurn,
    BlurBg,
    Concat,
    FadeIO,
    FilterGraph,
    OverlayAt,
    ReframeBlur,
    ReframeSplitBottom,
    Scale,
    ScaleCrop,
    SetPts,
    ShiftPts,
    TransitionIn,
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
                stdin=subprocess.DEVNULL,
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


_STILL = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_VIDEO = {".mp4", ".mov", ".webm", ".mkv"}


def overlay_input_args(path: str, dur: float) -> list[str]:
    """Input flags by kind: stills loop as frames, GIFs/videos repeat their animation."""
    suffix = Path(path).suffix.lower()
    t = ["-t", f"{dur:.3f}"]
    if suffix == ".gif":
        return ["-ignore_loop", "0", *t, "-i", path]
    if suffix in _VIDEO:
        return ["-stream_loop", "-1", *t, "-i", path]
    if suffix in _STILL:
        return ["-loop", "1", *t, "-i", path]
    raise RenderError("bad_overlay", f"unsupported overlay file type {suffix!r}")


def build_inputs(
    master_path: str, overlays: dict[str, float] | list[tuple[str, float]]
) -> list[str]:
    """Master input + one input per overlay (order = overlay label order)."""
    args = ["-i", master_path]
    items = overlays.items() if isinstance(overlays, dict) else overlays
    for path, dur in items:
        args += overlay_input_args(path, dur)
    return args


def _at(tl: Timeline, anchor_word: str, offset_ms: int = 0, where: str = "start") -> float:
    """Zooms land on a word's start; overlays appear as their word ends (schema)."""
    try:
        return snap.output_time_of(tl, anchor_word, where) + offset_ms / 1000.0
    except OpError as exc:
        raise RenderError("dangling_anchor", f"overlay/zoom anchor lost: {exc}") from None


def _drawn_last(window: tuple[Overlay, float, float]) -> bool:
    """Banners and cards are drawn after inserts so inserts never hide them."""
    overlay = window[0]
    return overlay.type in ("headline_card", "stat_card") or overlay.layout == "top_banner"


def build_video_filter(
    tl: Timeline,
    profile: RenderProfile,
    asset_labels: dict[str, str] | None = None,
    ass_path: str | None = None,
    reframe: str | None = None,
    overlay_windows: list[tuple[Overlay, float, float]] | None = None,
    zoom_windows: list[tuple[Zoom, float]] | None = None,
    after_cut: bool = False,  # slice renders: style this slice's incoming cut
    focus: Focus | None = None,  # face centre for split / face crops (None: centre)
) -> tuple[str, str]:
    """Compile clips + zooms + overlays + captions to (filter_script, video_out).

    overlay_windows/zoom_windows carry pre-clipped RELATIVE times (segment path);
    when None they are computed from anchors on the full timeline.
    """
    asset_labels = asset_labels or {}
    reframe = reframe or tl.reframe  # the timeline decides unless a caller overrides
    if overlay_windows is None:
        from app.render.segments import _adaptive_split_intervals, _chained_split_overlay_windows

        chained = {}
        if reframe in ("adaptive", "split_adaptive"):
            split_intervals = _adaptive_split_intervals(tl)
            chained = _chained_split_overlay_windows(tl, split_intervals)

        computed_overlays = []
        for overlay in tl.overlays:
            if overlay.id in chained:
                at, o_end = chained[overlay.id]
                computed_overlays.append((overlay, at, o_end))
            else:
                try:
                    computed_overlays.append((overlay, *snap.overlay_window(tl, overlay)))
                except OpError as exc:
                    raise RenderError("dangling_anchor", f"overlay anchor lost: {exc}") from None
        overlay_windows = computed_overlays

    has_split_top = any(o.layout == "split_top" for o, _, _ in overlay_windows)
    effective_reframe = reframe
    if reframe in ("adaptive", "split_adaptive"):
        effective_reframe = "split_bottom" if has_split_top else "blur"

    graph = FilterGraph(inputs=["[0:v]"])
    chains: list[str] = []
    cut_points = snap.transition_points(tl)
    for index, clip in enumerate(tl.video_clips):
        label = graph.add(Trim("[0:v]", start=clip.src_in, end=clip.src_out))
        if abs(clip.speed - 1.0) > 1e-9:
            label = graph.add(SetPts(_lbl(label), factor=1.0 / clip.speed))
        if effective_reframe in ("split_bottom", "split"):
            label = graph.add(ReframeSplitBottom(_lbl(label), profile.width, profile.height, focus))
        elif effective_reframe == "blur":
            label = graph.add(ReframeBlur(_lbl(label), profile.width, profile.height))
        else:  # center; face crops around the tracked face (centre without tracks)
            face = focus if effective_reframe == "face" else None
            label = graph.add(ScaleCrop(_lbl(label), profile.width, profile.height, face))
        styled = after_cut if index == 0 else index in cut_points
        if tl.transition != "none" and styled:
            label = graph.add(
                TransitionIn(_lbl(label), tl.transition, profile.width, profile.height)
            )
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
                hold_s=zoom.hold_ms / 1000.0,
            )
        )

    for overlay, at, end in sorted(overlay_windows, key=_drawn_last):
        source = asset_labels.get(overlay.id) or asset_labels.get(overlay.asset)
        if source is None:
            raise RenderError("unknown_asset", f"no local file for overlay asset {overlay.asset}")
        over = graph.add(ShiftPts(source, at))  # animation starts when the insert appears
        w, h = profile.width, profile.height
        if overlay.layout == "cutaway_full":
            scaled = graph.add(ScaleCrop(over, w, h))
            main = graph.add(OverlayAt(_lbl(main), _lbl(scaled), 0, 0, at, end))
        elif overlay.layout == "pip_pop":
            # Sticker in the safe upper-centre area (Reels UI covers the right rail).
            pip_w = int(w * 0.42)
            scaled = graph.add(Scale(over, pip_w, -2))
            faded = graph.add(FadeIO(_lbl(scaled), 0.12, 0.12, end - at, start=at))
            main = graph.add(OverlayAt(_lbl(main), _lbl(faded), "(W-w)/2", int(h * 0.16), at, end))
        elif overlay.layout == "split_top" and (
            overlay.type == "icon" or overlay.asset.startswith("icons:")
        ):
            # logo tile: contained and centred in the top panel (never cropped)
            side = int(h * 0.26)
            scaled = graph.add(Scale(over, side, side))
            faded = graph.add(FadeIO(_lbl(scaled), 0.1, 0.1, end - at, start=at))
            y = (h // 2 - side) // 2
            main = graph.add(OverlayAt(_lbl(main), _lbl(faded), "(W-w)/2", y, at, end))
        elif overlay.layout == "split_top":
            scaled = graph.add(ScaleCrop(over, w, h // 2))
            main = graph.add(OverlayAt(_lbl(main), _lbl(scaled), 0, 0, at, end))
        elif overlay.layout == "lower_third_card":
            scaled = graph.add(Scale(over, int(w * 0.85), -2))
            main = graph.add(OverlayAt(_lbl(main), _lbl(scaled), "(W-w)/2", h - 360, at, end))
        elif overlay.layout == "top_banner":
            scaled = graph.add(Scale(over, int(w * 0.90), -2))
            main = graph.add(OverlayAt(_lbl(main), _lbl(scaled), "(W-w)/2", int(h * 0.07), at, end))
        else:  # blur_bg_contain
            contained = graph.add(BlurBg(over, w, h))
            main = graph.add(OverlayAt(_lbl(main), _lbl(contained), 0, 0, at, end))

    if ass_path is not None and tl.captions.mode != "off":
        main = graph.add(AssBurn(_lbl(main), ass_path))
    return graph.compile()


def _lbl(label: str) -> str:
    """Ensure bracketed label."""
    return label if label.startswith("[") else f"[{label}]"
