"""faster-whisper ASR adapter (lazy: no torch/whisper import at module load).

Install for real transcription: pip install faster-whisper (GPU track, Phase 9).
"""

from app.analysis.adapters import ModelUnavailable
from app.timeline.transcript import Transcript, Word


class FasterWhisperTranscriber:
    """Word-timestamp transcription with language ID + override. Version: fw-tiny-1."""

    VERSION = "fw-tiny-1"

    def __init__(self, model_size: str = "tiny", device: str = "cpu") -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ModelUnavailable(
                "faster_whisper", "faster-whisper (CPU: + torch; GPU worker recommended)"
            ) from None
        self._model = WhisperModel(model_size, device=device)
        self._model_size = model_size

    async def transcribe(self, wav_path: str, language: str) -> Transcript:
        """Transcribe 16 kHz mono wav; language en | hi | auto."""
        import asyncio

        lang = None if language == "auto" else language
        segments, info = await asyncio.to_thread(
            self._model.transcribe, wav_path, language=lang, word_timestamps=True
        )
        detected = getattr(info, "language", language)
        words: list[Word] = []
        for seg in segments:
            for w in seg.words or []:
                words.append(
                    Word(
                        id=f"w{len(words) + 1}",
                        start=round(w.start, 3),
                        end=round(w.end, 3),
                        text=w.word.strip(),
                        display_text=w.word.strip(),
                        lang="hi" if detected == "hi" else "en",
                        confidence=float(getattr(w, "probability", 1.0)),
                    )
                )
        return Transcript(words=words, language=detected)
