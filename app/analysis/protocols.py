"""Stage protocols: every analyzer behind an interface; tests use fakes."""

from typing import Protocol, runtime_checkable

from app.analysis.models import (
    EmphasisMark,
    EnergyCurve,
    FaceBox,
    FillerHit,
    SceneCut,
    SpeechRange,
)
from app.timeline.transcript import Beat
from app.timeline.transcript import Transcript as _Transcript


@runtime_checkable
class Transcriber(Protocol):
    """Speech-to-text with word timestamps (faster-whisper or fake)."""

    async def transcribe(self, wav_path: str, language: str) -> _Transcript:
        """ASR over a 16 kHz mono wav; language en | hi | auto."""
        ...


@runtime_checkable
class Vad(Protocol):
    """Speech/silence segmentation."""

    def detect(self, wav_path: str) -> list[SpeechRange]:
        """Return speech ranges in source seconds."""
        ...


@runtime_checkable
class SceneDetector(Protocol):
    """Shot boundary detection."""

    def cuts(self, video_path: str) -> list[SceneCut]:
        """Return cut times in source seconds."""
        ...


@runtime_checkable
class EnergyAnalyzer(Protocol):
    """Loudness/energy curve."""

    def curve(self, wav_path: str) -> EnergyCurve:
        """RMS energy per 100 ms bucket."""
        ...


@runtime_checkable
class FillerFinder(Protocol):
    """Filler-word detection with per-language context rules."""

    def find(self, transcript: _Transcript) -> list[FillerHit]:
        """Return deletable filler candidates."""
        ...


@runtime_checkable
class EmphasisTagger(Protocol):
    """Prosody + LLM emphasis scoring."""

    async def tag(self, transcript: _Transcript, energy: EnergyCurve) -> list[EmphasisMark]:
        """Top emphasis marks (score-sorted)."""
        ...


@runtime_checkable
class BeatDetector(Protocol):
    """LLM text-only semantic beat pass."""

    async def detect(self, transcript: _Transcript) -> list[Beat]:
        """Return beats with transcript-anchored word IDs."""
        ...


@runtime_checkable
class FaceTracker(Protocol):
    """Face box tracks for reframing and face-avoidance."""

    def track(self, video_path: str) -> list[FaceBox]:
        """Per-sample face boxes (5 fps target)."""
        ...
