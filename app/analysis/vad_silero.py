"""Silero VAD adapter (lazy torch). Default pipeline uses EnergyVad instead."""

from app.analysis.adapters import ModelUnavailable
from app.analysis.models import SpeechRange


class SileroVad:
    """Neural VAD timestamps. Version: silero-1."""

    VERSION = "silero-1"

    def __init__(self) -> None:
        try:
            import torch  # noqa: F401
        except ImportError:
            raise ModelUnavailable(
                "torch", "torch --index-url https://download.pytorch.org/whl/cpu"
            ) from None
        self._torch = torch

    def detect(self, wav_path: str) -> list[SpeechRange]:
        """Speech ranges via Silero VAD (16 kHz wav)."""
        model, utils = self._torch.hub.load(
            repo_or_dir="snakers4/silero-vad", model="silero_vad", trust_repo=False
        )
        (get_speech_timestamps, _, _, _, _) = utils
        import wave

        with wave.open(wav_path, "rb") as w:
            assert w.getframerate() == 16000 and w.getnchannels() == 1
            import numpy as np

            samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype("float32")
            samples = samples / 32768.0
        stamps = get_speech_timestamps(samples, model, sampling_rate=16000)
        return [SpeechRange(start=s["start"] / 16000, end=s["end"] / 16000) for s in stamps]
