"""Typed filtergraph builder: nodes compile to one deterministic -filter_complex script.

Only typed fields enter the script. Free text NEVER enters here — captions go
through escaped ASS files (captions_ass.py), never through filter arguments.
"""

from dataclasses import dataclass, field
from typing import Protocol


def _s(seconds: float) -> str:
    """Millisecond-precise timestamp."""
    return f"{seconds:.3f}"


@dataclass(frozen=True)
class Trim:
    """Cut + timestamp reset."""

    input: str
    start: float
    end: float

    def render(self, out: str) -> str:
        return (
            f"{self.input}trim=start={_s(self.start)}:end={_s(self.end)},setpts=PTS-STARTPTS{out}"
        )


@dataclass(frozen=True)
class SetPts:
    """Speed: factor multiplies presentation timestamps (video)."""

    input: str
    factor: float

    def render(self, out: str) -> str:
        return f"{self.input}setpts={self.factor}*PTS{out}"


@dataclass(frozen=True)
class Concat:
    """Concatenate segments (identical params required)."""

    inputs: tuple[str, ...]
    n: int

    def render(self, out: str) -> str:
        return f"{''.join(self.inputs)}concat=n={self.n}:v=1:a=0{out}"


@dataclass(frozen=True)
class OverlayAt:
    """Overlay with enable window in seconds. x/y accept expressions."""

    main: str
    over: str
    x: int | str
    y: int | str
    start: float
    end: float

    def render(self, out: str) -> str:
        return (
            f"{self.main}{self.over}overlay={self.x}:{self.y}:"
            f"enable='between(t,{_s(self.start)},{_s(self.end)})'{out}"
        )


@dataclass(frozen=True)
class Scale:
    """Scale (w/h accept -2 for aspect)."""

    input: str
    w: int | str
    h: int | str

    def render(self, out: str) -> str:
        return f"{self.input}scale={self.w}:{self.h}{out}"


@dataclass(frozen=True)
class ReframeBlur:
    """Full-frame reframe: blurred fill background + contained foreground."""

    input: str
    width: int
    height: int

    def render(self, out: str) -> str:
        w, h = self.width, self.height
        return (
            f"{self.input}split[rb][rf];"
            f"[rb]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=20[lbg];"
            f"[rf]scale={w}:{h}:force_original_aspect_ratio=decrease[lfg];"
            f"[lbg][lfg]overlay=(W-w)/2:(H-h)/2{out}"
        )


@dataclass(frozen=True)
class BlurBg:
    """Contain an image over its own blurred fullscreen copy."""

    over: str
    width: int
    height: int

    def render(self, out: str) -> str:
        w, h = self.width, self.height
        return (
            f"{self.over}split[bg_in][fg_in];"
            f"[bg_in]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},boxblur=20[bg];"
            f"[bg][fg_in]overlay=(W-w)/2:(H-h)/2{out}"
        )


@dataclass(frozen=True)
class ScaleCrop:
    """Scale to fill then center-crop (reframe)."""

    input: str
    width: int
    height: int

    def render(self, out: str) -> str:
        return (
            f"{self.input}scale={self.width}:{self.height}:"
            f"force_original_aspect_ratio=increase,crop={self.width}:{self.height}{out}"
        )


@dataclass(frozen=True)
class FadeIO:
    """Fade in/out in seconds (overlay polish)."""

    input: str
    fade_in: float
    fade_out: float
    duration: float

    def render(self, out: str) -> str:
        return (
            f"{self.input}fade=t=in:st=0:d={_s(self.fade_in)},"
            f"fade=t=out:st={_s(self.duration - self.fade_out)}:d={_s(self.fade_out)}{out}"
        )


@dataclass(frozen=True)
class ZoomPunch:
    """Linear punch-in: scale 1 -> `scale` over `dur_s` starting at `at`.

    Animated scale + STATIC center crop (crop has no per-frame eval; the target
    size is constant so a fixed crop is exact).
    """

    input: str
    at: float
    dur_s: float
    scale: float
    width: int
    height: int

    def render(self, out: str) -> str:
        if self.scale <= 1.001:
            return f"{self.input}null{out}"
        t0, d, s = _s(self.at), _s(self.dur_s), self.scale
        z = f"(1+({s}-1)*clip((t-{t0})/{d}\\,0\\,1))"
        return f"{self.input}scale=iw*{z}:ih*{z}:eval=frame,crop={self.width}:{self.height}{out}"


@dataclass(frozen=True)
class AssBurn:
    """Burn an ASS subtitle file (escaped upstream). Path is ours, never external."""

    input: str
    ass_path: str

    def render(self, out: str) -> str:
        safe = self.ass_path.replace("'", "'\\''").replace(":", "\\:")
        return f"{self.input}ass='{safe}'{out}"


@dataclass(frozen=True)
class AudioChain:
    """Denoise (+ optional atempo list) for one audio input."""

    input: str
    atempo: tuple[float, ...] = ()

    def render(self, out: str) -> str:
        chain = "afftdn,aresample=async=1"
        for factor in self.atempo:
            chain += f",atempo={factor}"
        return f"{self.input}{chain}{out}"


class Node(Protocol):
    """A filter node rendering to one labeled chain segment."""

    def render(self, out: str) -> str:
        """Render using input labels baked into the node; emit to `out`."""
        ...


@dataclass
class FilterGraph:
    """Ordered node list compiling to a deterministic script with [vN] labels."""

    inputs: list[str]
    _nodes: list[tuple[Node, str]] = field(default_factory=list)
    _counter: int = 0

    def add(self, node: Node) -> str:
        """Append a node; returns its output label."""
        label = f"[v{self._counter}]"
        self._counter += 1
        self._nodes.append((node, label))
        return label

    def compile(self) -> tuple[str, str]:
        """(filter_script, final_label)."""
        parts: list[str] = []
        last = self.inputs[0] if self.inputs else ""
        for node, label in self._nodes:
            parts.append(node.render(label))
            last = label
        return (";".join(parts), last)
