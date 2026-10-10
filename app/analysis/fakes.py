"""Deterministic fakes: scripted outputs + call counters (cache-hit proofs)."""

from app.analysis.models import EmphasisMark, EnergyCurve, FaceBox, FillerHit, SceneCut, SpeechRange
from app.timeline.transcript import Beat, Transcript, Word


def _demo_transcript() -> Transcript:
    return Transcript(
        words=[
            Word(id="w1", start=0.0, end=0.4, text="hello", display_text="hello", lang="en"),
            Word(id="w2", start=0.5, end=0.9, text="world", display_text="world", lang="en"),
        ],
        language="en",
    )


class CountingFakeTranscriber:
    """Fake ASR returning a fixed 2-word transcript."""

    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, wav_path: str, language: str) -> Transcript:
        _ = (wav_path, language)
        self.calls += 1
        return _demo_transcript()


class FakeVad:
    """Fake VAD: whole file is speech."""

    def __init__(self, ranges: list[SpeechRange] | None = None) -> None:
        self.calls = 0
        self._ranges = ranges if ranges is not None else [SpeechRange(start=0.0, end=0.9)]

    def detect(self, wav_path: str) -> list[SpeechRange]:
        _ = wav_path
        self.calls += 1
        return self._ranges


class FakeScenes:
    """Fake scene detector: no cuts."""

    def __init__(self) -> None:
        self.calls = 0

    def cuts(self, video_path: str) -> list[SceneCut]:
        _ = video_path
        self.calls += 1
        return []


class FakeEnergy:
    """Fake energy: flat loud curve."""

    def __init__(self) -> None:
        self.calls = 0

    def curve(self, wav_path: str) -> EnergyCurve:
        _ = wav_path
        self.calls += 1
        return EnergyCurve(bucket_ms=100, values=[0.8] * 9)


class FakeFillers:
    """Fake filler finder: nothing deletable."""

    def __init__(self) -> None:
        self.calls = 0

    def find(self, transcript: Transcript) -> list[FillerHit]:
        _ = transcript
        self.calls += 1
        return []


class FakeEmphasis:
    """Fake emphasis: first word marked."""

    def __init__(self) -> None:
        self.calls = 0

    async def tag(self, transcript: Transcript, energy: EnergyCurve) -> list[EmphasisMark]:
        _ = energy
        self.calls += 1
        if not transcript.words:
            return []
        return [EmphasisMark(word_id=transcript.words[0].id, score=0.9)]


class FakeBeatDetector:
    """Fake beats: none (scripted detectors live in tests)."""

    def __init__(self) -> None:
        self.calls = 0

    async def detect(self, transcript: Transcript) -> list[Beat]:
        _ = transcript
        self.calls += 1
        return []


class FakeFaces:
    """Fake face tracker: no faces."""

    def __init__(self) -> None:
        self.calls = 0

    def track(self, video_path: str) -> list[FaceBox]:
        _ = video_path
        self.calls += 1
        return []
