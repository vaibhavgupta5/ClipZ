"""Waveform tests: peaks from raw PCM (no ffmpeg)."""

import numpy as np

from app.media.waveform import peaks_from_pcm, waveform_json


def test_silence_gives_zeros() -> None:
    pcm = np.zeros(16000, dtype="<f4").tobytes()
    peaks = peaks_from_pcm(pcm, 16000, bucket_ms=100)
    assert len(peaks) == 10
    assert all(p == 0.0 for p in peaks)


def test_full_scale_sine_peaks_near_one() -> None:
    t = np.arange(16000, dtype=np.float64) / 16000
    pcm = (0.99 * np.sin(2 * np.pi * 440 * t)).astype("<f4").tobytes()
    peaks = peaks_from_pcm(pcm, 16000, bucket_ms=100)
    assert len(peaks) == 10
    assert all(0.9 < p <= 1.0 for p in peaks)


def test_int16_supported() -> None:
    pcm = (np.ones(8000, dtype="<i2") * 32767).tobytes()
    peaks = peaks_from_pcm(pcm, 8000, bucket_ms=100, dtype="s16")
    assert len(peaks) == 10
    assert all(abs(p - 32767 / 32768) < 1e-9 for p in peaks)


def test_waveform_json_shape() -> None:
    doc = waveform_json([0.0, 0.5, 1.0], duration_s=0.3)
    assert doc == {"duration_s": 0.3, "bucket_ms": 100, "peaks": [0.0, 0.5, 1.0]}
