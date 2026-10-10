"""faster-whisper ASR adapter (CTranslate2; CPU int8 by default, CUDA when available).

The model is imported lazily and downloaded from Hugging Face on first use.
"""

from typing import Any

from app.analysis.adapters import ModelUnavailable
from app.timeline.transcript import Transcript, Word

ASR_LOGIC = "3"  # bump when decoding logic changes: part of the analysis cache key
SUPPORTED = ("en", "hi")
CONFIDENT = 0.8  # language-ID probability above which we trust an "en" detection


def asr_version(model_size: str) -> str:
    """Cache-key version for a model size (no model load needed)."""
    return f"fw-{model_size}-{ASR_LOGIC}"


def _mean_prob(segments: list[Any]) -> float:
    probs = [float(getattr(w, "probability", 0.0)) for s in segments for w in (s.words or [])]
    return sum(probs) / len(probs) if probs else 0.0


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
        self.VERSION = asr_version(model_size)  # part of the analysis cache key

    async def transcribe(self, wav_path: str, language: str) -> Transcript:
        """Transcribe 16 kHz mono wav; language en | hi | auto."""
        import asyncio

        lang = None if language == "auto" else language

        def _decode() -> tuple[list[Any], Any, str]:
            if lang is not None:
                segments, info = _run(lang)
                return segments, info, lang
            segments, info = _run(None)
            detected = getattr(info, "language", None)
            confidence = float(getattr(info, "language_probability", 0.0) or 0.0)
            if detected == "en" and confidence >= CONFIDENT:
                return segments, info, "en"
            # Uncertain or Hindi: Hinglish often decodes far better as Roman-script English
            # than as Devanagari. Decode both and keep the more confident transcript.
            candidates = {code: _run(code) for code in SUPPORTED}
            best = max(candidates, key=lambda code: _mean_prob(candidates[code][0]))
            label = "hinglish" if best == "en" and detected == "hi" else best
            return (*candidates[best], label)

        def _run(language_code: str | None) -> tuple[list[Any], Any]:
            # segments is a lazy generator: consume it here, inside the worker thread,
            # or the actual decoding runs on (and blocks) the event loop
            # Decode with our ffmpeg (f32le, 16 kHz mono) and pass samples: faster-whisper's
            # own PyAV path breaks on av>=19 (removed `metadata_errors`).
            audio = _load_pcm16k(wav_path)
            segments, info = self._model.transcribe(
                audio, language=language_code, word_timestamps=True, vad_filter=True
            )
            return list(segments), info

        segments, info, detected = await asyncio.to_thread(_decode)
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
                        lang="hi" if detected == "hi" else "en",  # hinglish = Roman script
                        confidence=float(getattr(w, "probability", 1.0)),
                    )
                )
        return Transcript(words=words, language=detected)
