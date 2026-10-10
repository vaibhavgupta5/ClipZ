"""Renderer integration tests: real ffmpeg preview renders. Opt-in (slow)."""

import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from app.media.probe import probe_file
from app.render.audio import measure_loudness
from app.render.edl_to_ffmpeg import PREVIEW
from app.render.render import render_timeline
from app.render.segments import split_segments
from app.settings import get_settings
from app.storage import get_storage
from app.timeline import ops
from app.timeline.schema import CaptionConfig, OutputSpec, Timeline, VideoClip
from app.timeline.transcript import Transcript, Word

pytestmark = pytest.mark.integration


def _words(n: int = 6) -> Transcript:
    salt = uuid.uuid4().hex[:6]  # unique display text per run -> fresh segment hashes
    return Transcript(
        words=[
            Word(
                id=f"w{i + 1}",
                start=i * 0.5,
                end=i * 0.5 + 0.4,
                text=t,
                display_text=f"{t}-{salt}",
                lang="en",
            )
            for i, t in enumerate(["a", "b", "c", "d", "e", "f"][:n])
        ]
    )


def _timeline(**overrides):  # type: ignore[no-untyped-def]
    base = dict(
        project_id=uuid.uuid4(),
        transcript=_words(),
        video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=3.0)],
        captions=CaptionConfig(),
        output=OutputSpec(),
    )
    base.update(overrides)
    return Timeline(**base)


def _master_bytes() -> bytes:
    return Path("fixtures/synth/bars_5s.mp4").read_bytes()


async def _render(tl: Timeline, profile=PREVIEW):  # type: ignore[no-untyped-def]
    import asyncio as _asyncio
    import tempfile

    store = get_storage(get_settings())
    with tempfile.TemporaryDirectory(prefix="reelforge-render-test-") as tmp:
        master = str(Path(tmp) / "master.mp4")
        await _asyncio.to_thread(Path(master).write_bytes, _master_bytes())
        return await render_timeline(tl, master, {}, profile, store, tmp, lambda f: None)


async def test_preview_render_facts() -> None:
    out = await _render(_timeline())
    assert out["key"].endswith(".mp4")
    data = get_storage(get_settings()).get_bytes(out["key"])
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
        path = f.name
    try:
        info = probe_file(path)
    finally:
        os.unlink(path)
    assert (info.width, info.height) == (540, 960)
    assert abs(info.fps - 30.0) < 0.01
    assert abs(info.duration_s - 3.0) < 0.04
    assert info.audio_channels == 2


async def test_loudness_and_drift_after_cuts() -> None:
    tl = _timeline()
    for wid in ["w2", "w4"]:
        tl, _ = ops.delete_words(tl, [wid])
    assert len(tl.video_clips) == 3  # 2 holes -> 3 clips
    out = await _render(tl)
    data = get_storage(get_settings()).get_bytes(out["key"])

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
        path = f.name
    try:
        probe_file(path)  # validates container readability
        loud = measure_loudness(path)
        assert abs(float(loud["input_i"]) - (-14.0)) <= 1.0
        # A/V drift under 40 ms: compare per-stream durations
        import asyncio as _asyncio2
        import json
        import subprocess

        def _probe_durations() -> list[float]:
            doc = json.loads(
                subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "stream=duration",
                        "-of",
                        "json",
                        path,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
            )
            return [float(s["duration"]) for s in doc["streams"]]

        durations = await _asyncio2.to_thread(_probe_durations)
        assert max(durations) - min(durations) < 0.04
    finally:
        os.unlink(path)


async def test_caption_change_rerenders_one_segment() -> None:
    tl = _timeline()
    assert len(split_segments(tl)) == 1
    tl2, _ = ops.split(tl, "w4")  # 2 segments
    assert len(split_segments(tl2)) == 2

    out1 = await _render(tl2)
    assert out1["stats"]["misses"] == 2
    # change a caption word in segment 2 only (display text, same timings)
    words = [
        w.model_copy(update={"display_text": "ZZZ"}) if w.id == "w5" else w
        for w in tl2.transcript.words
    ]
    tl3 = tl2.model_copy(update={"transcript": tl2.transcript.model_copy(update={"words": words})})
    out2 = await _render(tl3)
    assert out2["stats"]["misses"] == 1, out2["stats"]
    assert out2["stats"]["hits"] == 1


async def test_animated_and_still_inserts_keep_the_timeline_duration() -> None:
    """Overlay inputs must last exactly their window (regression: video ran 2x long)."""
    import asyncio as _asyncio

    from app.timeline.schema import Overlay

    tl, _ = ops.split(_timeline(), "w3")  # two segments, inserts in the second
    store = get_storage(get_settings())
    with tempfile.TemporaryDirectory(prefix="reelforge-ov-test-") as tmp:
        gif = Path(tmp) / "sticker.gif"
        await _asyncio.to_thread(
            subprocess.run,
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=120x120:rate=10:duration=2",
                str(gif),
            ],
            check=True,
        )
        overlays = [
            Overlay(
                id="g1",
                type="gif",
                asset="gif",
                anchor_word="w3",
                duration_ms=1000,
                layout="pip_pop",
                license="CC0-1.0",
            ),
            Overlay(
                id="c1",
                type="image",
                asset="card",
                anchor_word="w5",
                duration_ms=600,
                layout="blur_bg_contain",
                license="CC0-1.0",
            ),
        ]
        tl = tl.model_copy(update={"overlays": overlays})
        master = str(Path(tmp) / "master.mp4")
        await _asyncio.to_thread(Path(master).write_bytes, _master_bytes())
        files = {"gif": str(gif), "card": "fixtures/synth/card_red.png"}
        out = await render_timeline(tl, master, files, PREVIEW, store, tmp, lambda f: None)
        path = Path(tmp) / "out.mp4"
        path.write_bytes(store.get_bytes(str(out["key"])))
        probe = (
            await _asyncio.to_thread(
                subprocess.run,
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_type,duration",
                    "-of",
                    "csv=p=0",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        ).stdout.split()
    durations = {line.split(",")[0]: float(line.split(",")[1]) for line in probe}
    assert abs(durations["video"] - tl.duration_s) < 0.1, durations
    assert abs(durations["audio"] - tl.duration_s) < 0.1, durations


@pytest.mark.parametrize("style", ["zoom_in", "flash", "whip"])
async def test_cut_transitions_render(style: str) -> None:
    """Every transition style is accepted by ffmpeg and keeps the timeline duration."""
    tl, _ = ops.delete_words(_timeline(), ["w3"])  # one cut -> two segments
    tl = tl.model_copy(update={"transition": style})
    out = await _render(tl)
    data = get_storage(get_settings()).get_bytes(out["key"])
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        info = probe_file(path)
    finally:
        os.unlink(path)
    assert abs(info.duration_s - tl.duration_s) < 0.1
