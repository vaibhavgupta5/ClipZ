"""faster-whisper ASR adapter (CTranslate2; CPU int8 by default, CUDA when available).

The model is imported lazily and downloaded from Hugging Face on first use.
"""

from typing import Any

from app.analysis.adapters import ModelUnavailable
from app.timeline.transcript import Transcript, Word

SUPPORTED = ("en", "hi")
MIN_WORD_S = 0.02


def _load_pcm16k(path: str) -> Any:
    """16 kHz mono float32 samples via ffmpeg (argument list, no shell)."""
    import numpy as np

    from app.media.ffmpeg import ffmpeg_path, run_capture

    pcm = run_capture(
        [
            ffmpeg_path(),
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-i",
            path,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "f32le",
            "-",
        ]
    )
    return np.frombuffer(pcm, dtype=np.float32)


class FasterWhisperTranscriber:
    """Word-timestamp transcription with language ID + override."""

    def __init__(self, model_size: str = "small", device: str = "cpu") -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ModelUnavailable(
                "faster_whisper", "faster-whisper (uv add faster-whisper; CPU int8 works)"
            ) from None
        compute = "int8" if device == "cpu" else "float16"
        self._model = WhisperModel(model_size, device=device, compute_type=compute)
        self._model_size = model_size
        self.VERSION = f"fw-{model_size}-2"  # model is part of the analysis cache key

    async def transcribe(self, wav_path: str, language: str) -> Transcript:
        """Transcribe 16 kHz mono wav; language en | hi | auto."""
        import asyncio

        lang = None if language == "auto" else language

        def _decode() -> tuple[list[Any], Any]:
            segments, info = _run(lang)
            detected = getattr(info, "language", lang)
            if lang is None and detected not in SUPPORTED:
                # Product languages are en/hi only: re-decode as the likelier of the two.
                probs = dict(getattr(info, "all_language_probs", None) or [])
                best = max(SUPPORTED, key=lambda code: probs.get(code, 0.0))
                segments, info = _run(best)
            return segments, info

        def _run(language_code: str | None) -> tuple[list[Any], Any]:
            # segments is a lazy generator: consume it here, inside the worker thread,
            # or the actual decoding runs on (and blocks) the event loop
            # Decode with our ffmpeg (f32le, 16 kHz mono) and pass samples: faster-whisper's
            # own PyAV path breaks on av>=19 (removed `metadata_errors`).
            audio = _load_pcm16k(wav_path)
            segments, info = self._model.transcribe(
                audio, language=language_code, word_timestamps=True, vad_filter=False
            )
            return list(segments), info

        segments, info = await asyncio.to_thread(_decode)
        detected = getattr(info, "language", None) or (lang or "en")
        if detected not in SUPPORTED:
            detected = "en"
        words: list[Word] = []
        for seg in segments:
            for w in seg.words or []:
                text = w.word.strip()
                if not text:
                    continue
                start = round(float(w.start), 3)
                # Whisper can emit zero-length words; give them a minimal span instead
                # of failing the whole transcript.
                end = max(round(float(w.end), 3), round(start + MIN_WORD_S, 3))
                words.append(
                    Word(
                        id=f"w{len(words) + 1}",
                        start=start,
                        end=end,
                        text=text,
                        display_text=text,
                        lang="hi" if detected == "hi" else "en",
                        confidence=float(getattr(w, "probability", 1.0)),
                    )
                )
        return Transcript(words=words, language=detected)
