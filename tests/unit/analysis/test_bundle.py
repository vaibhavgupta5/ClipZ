"""Analysis bundle tests: models, cache keys, call-counting fakes."""

from app.analysis.fakes import CountingFakeTranscriber, FakeBeatDetector, FakeVad
from app.analysis.models import (
    AnalysisBundle,
    EmphasisMark,
    EnergyCurve,
    FillerHit,
    SceneCut,
    SpeechRange,
    cache_key,
)
from app.timeline.transcript import Transcript, Word


def _transcript() -> Transcript:
    return Transcript(
        words=[Word(id="w1", start=0.0, end=0.4, text="a", display_text="a", lang="en")]
    )


def _bundle() -> AnalysisBundle:
    return AnalysisBundle(
        transcript=_transcript(),
        speech=[SpeechRange(start=0.0, end=0.4)],
        scenes=[SceneCut(at=1.0)],
        energy=EnergyCurve(bucket_ms=100, values=[0.9]),
        fillers=[FillerHit(word_id="w1", filler="um")],
        emphasis=[EmphasisMark(word_id="w1", score=0.8)],
        beats=[],
        faces=[],
        language="en",
        analyzer_versions={"vad": "energy-1"},
    )


def test_bundle_round_trips_through_json() -> None:
    dumped = _bundle().model_dump(mode="json")
    assert AnalysisBundle.model_validate(dumped).language == "en"


def test_cache_key_changes_with_version_and_params() -> None:
    k1 = cache_key("sha", "vad", "1", {"t": 0.5})
    assert k1 != cache_key("sha", "vad", "2", {"t": 0.5})
    assert k1 != cache_key("sha", "vad", "1", {"t": 0.6})
    assert k1 != cache_key("other", "vad", "1", {"t": 0.5})
    assert k1.startswith("analysis/sha/")


def test_fakes_count_calls_for_cache_proof() -> None:
    vad = FakeVad()
    assert vad.calls == 0
    vad.detect("/tmp/x.wav")
    assert vad.calls == 1


async def test_counting_transcriber_and_beats() -> None:
    tr = CountingFakeTranscriber()
    t = await tr.transcribe("/tmp/x.wav", "en")
    assert tr.calls == 1 and t.words[0].id == "w1"
    beats = await FakeBeatDetector().detect(t)
    assert beats == []
