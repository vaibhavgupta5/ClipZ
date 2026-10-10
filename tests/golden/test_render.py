"""Golden render tests: ffprobe facts + SSIM vs stored reference frames.

References live in fixtures/golden/. First run bootstraps them (logs a notice);
commit the PNGs. Deterministic pipeline (threads=1) keeps SSIM >= 0.98.
"""

import re
import subprocess
import uuid
from pathlib import Path

import pytest

from app.render.edl_to_ffmpeg import PREVIEW
from app.render.render import render_timeline
from app.settings import get_settings
from app.storage import get_storage
from app.timeline import ops
from app.timeline.schema import CaptionConfig, OutputSpec, Overlay, Timeline, VideoClip, Zoom
from app.timeline.transcript import Transcript, Word

GOLDEN_DIR = Path("fixtures/golden")
SSIM_FLOOR = 0.98


def _words(texts: list[str], lang: str = "en") -> Transcript:
    words = [
        Word(
            id=f"w{i + 1}",
            start=i * 0.5,
            end=i * 0.5 + 0.4,
            text=t,
            display_text=t,
            lang="en" if lang == "en" else "hi",
        )
        for i, t in enumerate(texts)
    ]
    return Transcript(words=words)


def _base_tl(layout: str | None = None, hindi: bool = False, zoom: bool = False) -> Timeline:
    texts = (
        ["namaste", "duniya", "aaj", "mausam", "achha", "hai"]
        if hindi
        else ["a", "b", "c", "d", "e", "f"]
    )
    lang = "hi" if hindi else "en"
    tl = Timeline(
        project_id=uuid.uuid4(),
        transcript=_words(texts, lang),
        video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=3.0)],
        captions=CaptionConfig(),
        output=OutputSpec(),
    )
    if zoom:
        tl, _ = ops.add_zoom(tl, Zoom(id="z1", anchor_word="w2", scale=1.15, dur_ms=400))
    if layout:
        tl, _ = ops.add_overlay(
            tl,
            Overlay(
                id="ins1",
                type="image",
                asset="card",
                anchor_word="w2",
                offset_ms=0,
                duration_ms=1500,
                layout=layout,
                license="CC0-1.0",  # type: ignore[arg-type]
            ),
        )
    return tl


def _ssim(ref: Path, frame: Path) -> float:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(ref),
            "-i",
            str(frame),
            "-lavfi",
            "ssim",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"All:([0-9.]+)", proc.stderr)
    assert match, f"no SSIM score in: {proc.stderr[-500:]}"
    return float(match.group(1))


async def _render_frame(
    tl: Timeline, asset_files: dict[str, str], at: float, ref_name: str
) -> Path:
    import asyncio as _asyncio
    import os
    import tempfile

    GOLDEN_DIR.mkdir(exist_ok=True)
    ref = GOLDEN_DIR / ref_name
    store = get_storage(get_settings())
    with tempfile.TemporaryDirectory(prefix="reelforge-golden-") as tmp:
        master = str(Path(tmp) / "master.mp4")
        await _asyncio.to_thread(
            Path(master).write_bytes, Path("fixtures/synth/bars_5s.mp4").read_bytes()
        )
        out = await render_timeline(tl, master, asset_files, PREVIEW, store, tmp, lambda f: None)
        data = store.get_bytes(str(out["key"]))
        rendered = Path(tmp) / "rendered.mp4"
        await _asyncio.to_thread(rendered.write_bytes, data)
        frame = Path(tmp) / "frame.png"

        def _extract() -> None:
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    str(at),
                    "-i",
                    str(rendered),
                    "-frames:v",
                    "1",
                    str(frame),
                ],
                check=True,
            )

        await _asyncio.to_thread(_extract)
        if not ref.exists():
            await _asyncio.to_thread(Path(ref).write_bytes, frame.read_bytes())
            print(f"\n[golden] bootstrapped {ref}")
        score = await _asyncio.to_thread(_ssim, ref, frame)
        assert score >= SSIM_FLOOR, f"{ref_name}: SSIM {score} < {SSIM_FLOOR}"
        if os.environ.get("GOLDEN_UPDATE") == "1":
            await _asyncio.to_thread(Path(ref).write_bytes, frame.read_bytes())
    return ref


def _cards() -> dict[str, str]:
    return {"card": "fixtures/synth/card_red.png"}


async def test_golden_plain_cut() -> None:
    tl, _ = ops.split(_base_tl(), "w4")
    await _render_frame(tl, {}, 0.5, "cut.png")


async def test_golden_captions_en() -> None:
    await _render_frame(_base_tl(), {}, 1.3, "captions_en.png")


async def test_golden_captions_hi() -> None:
    await _render_frame(_base_tl(hindi=True), {}, 0.2, "captions_hi.png")


async def test_golden_zoom() -> None:
    await _render_frame(_base_tl(zoom=True), {}, 0.6, "zoom.png")


LAYOUTS = ["cutaway_full", "pip_pop", "split_top", "lower_third_card", "blur_bg_contain"]


@pytest.mark.parametrize("layout", LAYOUTS)
async def test_golden_overlay_layouts(layout: str) -> None:
    # mid-window: the insert is fully faded in (word w2 ends at 0.9 s, shown 0.9-2.4 s)
    await _render_frame(_base_tl(layout=layout), _cards(), 1.5, f"overlay_{layout}.png")
