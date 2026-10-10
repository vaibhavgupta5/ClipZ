"""Silero VAD via faster-whisper's bundled ONNX model (no torch, no download)."""

from app.analysis.adapters import ModelUnavailable
from app.analysis.models import SpeechRange


class SileroVad:
    """Neural speech ranges; robust to background music/chatter where energy VAD is not."""

    VERSION = "silero-onnx-1"

    def __init__(self, min_silence_ms: int = 300, pad_ms: int = 60) -> None:
        try:
            from faster_whisper.vad import VadOptions, get_speech_timestamps
        except ImportError:
            raise ModelUnavailable("faster_whisper", "uv add faster-whisper") from None
        self._get = get_speech_timestamps
        self._opts = VadOptions(min_silence_duration_ms=min_silence_ms, speech_pad_ms=pad_ms)

    def detect(self, wav_path: str) -> list[SpeechRange]:
        """Speech ranges (seconds) for a 16 kHz mono wav."""
        from app.analysis.asr_whisper import _load_pcm16k

        audio = _load_pcm16k(wav_path)
        stamps = self._get(audio, self._opts)
        return [SpeechRange(start=s["start"] / 16000, end=s["end"] / 16000) for s in stamps]
