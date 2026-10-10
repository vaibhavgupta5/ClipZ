"""Signal stage tests: energy VAD, ffmpeg scenes, RMS energy, filler rules."""

import wave

import numpy as np

from app.analysis.energy import EnergyAnalyzer
from app.analysis.fillers import FillerFinder
from app.analysis.scenes import SceneDetector
from app.analysis.vad import EnergyVad
from app.timeline.transcript import Transcript, Word


def _write_wav(path, samples: np.ndarray, rate: int = 16000) -> None:  # type: ignore[no-untyped-def]
    pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16)


def _sine(seconds: float, freq: float = 440.0, rate: int = 16000) -> np.ndarray:
    t = np.arange(int(seconds * rate), dtype=np.float64) / rate
    return 0.5 * np.sin(2 * np.pi * freq * t)


def _words(*texts: str) -> Transcript:
    return Transcript(
        words=[
            Word(
                id=f"w{i + 1}", start=i * 0.5, end=i * 0.5 + 0.4, text=t, display_text=t, lang="en"
            )
            for i, t in enumerate(texts)
        ]
    )


def test_vad_silence_has_no_speech(tmp_path) -> None:  # type: ignore[no-untyped-def]
    wav = tmp_path / "sil.wav"
    _write_wav(wav, np.zeros(16000))
    assert EnergyVad().detect(str(wav)) == []


def test_vad_tone_is_all_speech(tmp_path) -> None:  # type: ignore[no-untyped-def]
    wav = tmp_path / "tone.wav"
    _write_wav(wav, _sine(1.0))
    ranges = EnergyVad().detect(str(wav))
    assert len(ranges) == 1
    assert ranges[0].start == 0.0 and ranges[0].end == 0.98  # 98 frames x 10 ms


def test_vad_hysteresis_bridges_short_gaps(tmp_path) -> None:  # type: ignore[no-untyped-def]
    wav = tmp_path / "gap.wav"
    tone = 0.5 * np.ones(16000)
    tone[4000:5600] = 0.0  # 100 ms gap
    _write_wav(wav, tone)
    ranges = EnergyVad().detect(str(wav))
    assert len(ranges) == 1  # 100 ms gap bridged by hangover


def test_scenes_deterministic_on_fixture() -> None:
    det = SceneDetector(threshold=0.4)
    first = det.cuts("fixtures/synth/bars_5s.mp4")
    assert det.cuts("fixtures/synth/bars_5s.mp4") == first


def test_energy_curve_shape() -> None:
    curve = EnergyAnalyzer().curve("fixtures/synth/bars_5s.mp4")
    assert curve.bucket_ms == 100 and len(curve.values) == 50
    assert all(v > 0.05 for v in curve.values)  # continuous tone: no silent buckets


def test_filler_mid_sentence_flagged_edges_kept() -> None:
    hits = FillerFinder("en").find(_words("um", "hello", "world"))
    assert [h.word_id for h in hits] == ["w1"]  # leading interjection is deletable
    assert FillerFinder("en").find(_words("hello", "world")) == []
    assert FillerFinder("en").find(_words("hello")) == []  # single word never filler


def test_hindi_fillers() -> None:
    hits = FillerFinder("hi").find(_words("matlab", "yeh", "hai"))
    assert [h.word_id for h in hits] == ["w1"]
