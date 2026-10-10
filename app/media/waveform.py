"""Waveform peaks from decoded mono PCM (for the future app's waveform UI)."""

from typing import Literal

import numpy as np

PcmDtype = Literal["f32", "s16"]


def peaks_from_pcm(
    samples: bytes, sample_rate: int, bucket_ms: int = 100, dtype: PcmDtype = "f32"
) -> list[float]:
    """Peak amplitude per bucket, 0..1. Supports little-endian f32 and s16 mono."""
    if sample_rate <= 0 or bucket_ms <= 0:
        raise ValueError("sample_rate and bucket_ms must be positive")
    raw = np.frombuffer(samples, dtype="<f4" if dtype == "f32" else "<i2").astype(np.float64)
    if dtype == "s16":
        raw = raw / 32768.0
    per_bucket = max(1, int(sample_rate * bucket_ms / 1000))
    peaks: list[float] = []
    for start in range(0, len(raw), per_bucket):
        chunk = raw[start : start + per_bucket]
        peaks.append(float(np.max(np.abs(chunk))) if len(chunk) else 0.0)
    return [min(1.0, max(0.0, p)) for p in peaks]


def waveform_json(peaks: list[float], duration_s: float, bucket_ms: int = 100) -> dict[str, object]:
    """JSON document stored as waveform.json next to the proxy."""
    return {"duration_s": duration_s, "bucket_ms": bucket_ms, "peaks": peaks}
