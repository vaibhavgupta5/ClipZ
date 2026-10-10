"""Real ffmpeg run: dub replaces the voice, voiceover mixes in with the voice ducked."""

import shutil
from pathlib import Path

import pytest

from app.media.ffmpeg import ffmpeg_path, run_ffmpeg
from app.media.probe import probe_duration_s
from app.render.render import _render_audio
from app.timeline.schema import DubTrack, Voiceover
from tests.unit.timeline.test_ops import _timeline

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _tone(path: Path, freq: int, seconds: float) -> str:
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-f", "lavfi", "-i",
         f"sine=frequency={freq}:duration={seconds}", "-ac", "2", str(path)]
    )  # fmt: skip
    return str(path)


def test_dub_and_voiceover_render(tmp_path: Path) -> None:
    master = _tone(tmp_path / "master.wav", 220, 3.0)
    dub = _tone(tmp_path / "dub.mp3", 330, 3.0)
    vo = _tone(tmp_path / "vo.mp3", 440, 1.0)
    tl = _timeline().model_copy(
        update={
            "dub": DubTrack(language="hi", asset="generated:dub", license="ElevenLabs-paid-plan"),
            "voiceovers": [
                Voiceover(
                    id="vo1",
                    asset="generated:vo",
                    text="hello",
                    anchor_word="w1",
                    duration_ms=1000,
                    license="ElevenLabs-paid-plan",
                )
            ],
        }
    )
    files = {"generated:dub": dub, "generated:vo": vo}
    out = _render_audio(tl, master, tmp_path, None, files)
    assert probe_duration_s(out) == pytest.approx(3.0, abs=0.1)
