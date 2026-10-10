"""Real ffmpeg: a music bed is looped, ducked under speech, mixed and normalised. Opt-in."""

import asyncio
import math
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from app.media.probe import probe_file
from app.render.audio import measure_loudness
from app.render.edl_to_ffmpeg import PREVIEW
from app.render.render import render_timeline
from app.settings import get_settings
from app.storage import get_storage
from app.timeline.schema import MusicBed, Timeline, VideoClip
from app.timeline.transcript import Transcript, Word

pytestmark = pytest.mark.integration
SPEECH = Path("fixtures/synth/speech_en.mp4")


def _band_rms_db(path: str, lo: int, hi: int) -> float:
    """Mean RMS level (dB) of one frequency band."""
    out = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-i",
            path,
            "-af",
            f"highpass=f={lo},lowpass=f={hi},astats=metadata=0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stderr
    return float(re.findall(r"RMS level dB: (-?[\d.]+|-inf)", out)[-1])


@pytest.mark.skipif(not SPEECH.exists(), reason="run SPEECH=1 scripts/make_fixtures.sh")
async def test_music_bed_under_speech() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        music = Path(tmp) / "bed.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=4",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(music),
            ],
            check=True,
        )
        tl = Timeline(
            project_id=uuid.uuid4(),
            transcript=Transcript(
                words=[Word(id="w1", start=0.5, end=0.9, text="x", display_text="x", lang="en")]
            ),
            video_clips=[VideoClip(id="c1", asset="master", src_in=0.0, src_out=10.0)],
            music=MusicBed(asset="music:bed", license="CC0-1.0", level_db=-20.0),
        )
        store = get_storage(get_settings())

        async def render(timeline: Timeline, name: str) -> str:
            files = {"music:bed": str(music)}
            out = await render_timeline(
                timeline, str(SPEECH), files, PREVIEW, store, tmp, lambda f: None
            )
            path = Path(tmp) / name
            path.write_bytes(store.get_bytes(str(out["key"])))
            return str(path)

        mixed = await render(tl, "mixed.mp4")
        dry = await render(tl.model_copy(update={"music": None}), "dry.mp4")
        info = await asyncio.to_thread(probe_file, mixed)
        assert abs(info.duration_s - 10.0) < 0.05 and info.audio_channels == 2
        lufs = float((await asyncio.to_thread(measure_loudness, mixed))["input_i"])
        assert abs(lufs - (-14.0)) <= 1.0
        # Music-only power in its band = mixed band power minus the dry (voice-only) band.
        with_bed, without = _band_rms_db(mixed, 200, 240), _band_rms_db(dry, 200, 240)
        music_db = 10 * math.log10(max(1e-12, 10 ** (with_bed / 10) - 10 ** (without / 10)))
        voice_db = _band_rms_db(dry, 80, 4000)
        assert music_db > -60, "music bed missing (4 s bed should loop to 10 s)"
        assert voice_db - music_db >= 10, (voice_db, music_db)


@pytest.mark.skipif(not SPEECH.exists(), reason="run SPEECH=1 scripts/make_fixtures.sh")
async def test_sfx_hit_lands_at_its_time() -> None:
    from app.timeline.schema import SfxHit

    with tempfile.TemporaryDirectory() as tmp:
        hit = Path(tmp) / "beep.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=3000:duration=0.3",
                "-ar",
                "48000",
                str(hit),
            ],
            check=True,
        )
        word = Word(id="w1", start=2.0, end=2.5, text="x", display_text="x", lang="en")
        tl = Timeline(
            project_id=uuid.uuid4(),
            transcript=Transcript(words=[word]),
            video_clips=[VideoClip(id="c1", asset="master", src_in=0.0, src_out=6.0)],
            sfx=[
                SfxHit(
                    id="s1",
                    asset="sfx:beep",
                    anchor_word="w1",
                    offset_ms=0,
                    gain_db=-3.0,
                    license="CC0",
                )
            ],
        )
        store = get_storage(get_settings())
        out = await render_timeline(
            tl, str(SPEECH), {"sfx:beep": str(hit)}, PREVIEW, store, tmp, lambda f: None
        )
        path = Path(tmp) / "o.mp4"
        path.write_bytes(store.get_bytes(str(out["key"])))

        def band_at(start: float) -> float:
            res = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-ss",
                    str(start),
                    "-t",
                    "0.3",
                    "-i",
                    str(path),
                    "-af",
                    "highpass=f=2800,lowpass=f=3200,astats=metadata=0",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stderr
            return float(
                re.findall(r"RMS level dB: (-?[\d.]+|-inf)", res)[-1].replace("-inf", "-200")
            )

        # the 3 kHz beep is loud at 2.5 s (word end) and absent a second earlier; speech
        # sibilance also has 3 kHz energy, so the baseline is not silent
        assert band_at(2.5) - band_at(1.2) >= 8, (band_at(2.5), band_at(1.2))
