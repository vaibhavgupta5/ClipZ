"""Energy VAD with hysteresis: dual threshold + hangover bridges short gaps."""

import numpy as np

from app.analysis.models import SpeechRange

FRAME_S = 0.025
HOP_S = 0.010
HIGH_DB = -35.0
LOW_DB = -45.0
HANGOVER_S = 0.200
MIN_SPEECH_S = 0.150


class EnergyVad:
    """Speech/silence from frame energy (no model weights). Version: energy-1."""

    VERSION = "energy-1"

    def __init__(
        self,
        sample_rate: int = 16000,
        high_db: float = HIGH_DB,
        low_db: float = LOW_DB,
        hangover_s: float = HANGOVER_S,
    ) -> None:
        self.sample_rate = sample_rate
        self.high_db = high_db
        self.low_db = low_db
        self.hangover_s = hangover_s

    def _frame_db(self, pcm: bytes) -> np.ndarray:
        samples = np.frombuffer(pcm, dtype="<f4").astype(np.float64)
        frame = max(1, int(self.sample_rate * FRAME_S))
        hop = max(1, int(self.sample_rate * HOP_S))
        if len(samples) < frame:
            samples = np.pad(samples, (0, frame - len(samples)))
        n = 1 + (len(samples) - frame) // hop
        idx = np.arange(frame)[None, :] + np.arange(n)[:, None] * hop
        rms = np.sqrt(np.mean(samples[idx] ** 2, axis=1) + 1e-12)
        return 20 * np.log10(rms)

    def detect(self, wav_path: str) -> list[SpeechRange]:
        """Return speech ranges; pure silence yields []. Accepts any ffmpeg-readable audio."""
        from app.media.ffmpeg import ffmpeg_path, run_capture

        pcm = run_capture(
            [
                ffmpeg_path(),
                "-v",
                "error",
                "-i",
                wav_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "f32le",
                "-",
            ]
        )
        db = self._frame_db(pcm)
        hop = HOP_S
        hangover_frames = int(self.hangover_s / hop)
        ranges: list[SpeechRange] = []
        in_speech = False
        start = 0.0
        quiet_run = 0
        for i, level in enumerate(db):
            t = i * hop
            if not in_speech:
                if level >= self.high_db:
                    in_speech = True
                    start = t
                    quiet_run = 0
            elif level >= self.low_db:
                quiet_run = 0
            else:
                quiet_run += 1
                if quiet_run > hangover_frames:
                    end = t - quiet_run * hop + hop
                    if end - start >= MIN_SPEECH_S:
                        ranges.append(SpeechRange(start=round(start, 3), end=round(end, 3)))
                    in_speech = False
        if in_speech:
            end = len(db) * hop
            if end - start >= MIN_SPEECH_S:
                ranges.append(SpeechRange(start=round(start, 3), end=round(end, 3)))
        return ranges
