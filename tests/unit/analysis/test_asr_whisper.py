"""faster-whisper adapter edge cases with a fake model (no download, no audio decode)."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.analysis import asr_whisper
from app.analysis.asr_whisper import FasterWhisperTranscriber


class _FakeModel:
    def __init__(self, detected: str) -> None:
        self.detected = detected
        self.calls: list[str | None] = []

    def detect_language(self, audio: Any, **_: Any) -> tuple[str, float, list[Any]]:
        return self.detected, 0.7, []

    def transcribe(self, audio: Any, language: str | None, **_: Any) -> tuple[Any, Any]:
        self.calls.append(language)
        lang = language or self.detected
        words = [
            SimpleNamespace(word=" hello", start=0.0, end=0.4, probability=0.9),
            SimpleNamespace(word=" uh", start=0.74, end=0.74, probability=0.03),  # zero-length
            SimpleNamespace(word="  ", start=0.8, end=0.9, probability=0.5),  # blank
        ]
        info = SimpleNamespace(
            language=lang, all_language_probs=[("pt", 0.7), ("hi", 0.2), ("en", 0.1)]
        )
        return iter([SimpleNamespace(words=words)]), info


def _transcriber(model: _FakeModel, monkeypatch: pytest.MonkeyPatch) -> FasterWhisperTranscriber:
    monkeypatch.setattr(asr_whisper, "_load_pcm16k", lambda path: b"")
    tr = FasterWhisperTranscriber.__new__(FasterWhisperTranscriber)
    tr._model = model  # type: ignore[attr-defined]
    return tr


async def test_zero_length_and_blank_words_are_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    tx = await _transcriber(_FakeModel("en"), monkeypatch).transcribe("x.wav", "en")
    assert [w.text for w in tx.words] == ["hello", "uh"]
    assert tx.words[1].end == pytest.approx(0.76)  # 20 ms minimum span


async def test_unsupported_language_redecodes_as_likelier_of_en_hi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _FakeModel("pt")
    tx = await _transcriber(model, monkeypatch).transcribe("x.wav", "auto")
    assert model.calls == ["en", "hi"]  # en not confident enough -> hi tried too
    assert tx.language == "en"  # equal confidence keeps the Roman decode


async def test_explicit_language_is_not_redecoded(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _FakeModel("pt")
    await _transcriber(model, monkeypatch).transcribe("x.wav", "en")
    assert model.calls == ["en"]


class _Hinglish:
    """Detects hi with low confidence; en decode is confident, hi decode is garbage."""

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    def detect_language(self, audio: Any, **_: Any) -> tuple[str, float, list[Any]]:
        return "hi", 0.69, []

    def transcribe(self, audio: Any, language: str | None, **_: Any) -> tuple[Any, Any]:
        self.calls.append(language)
        prob = {"en": 0.85, "hi": 0.2}[str(language)]
        words = [SimpleNamespace(word=" bhai", start=0.0, end=0.3, probability=prob)]
        info = SimpleNamespace(language="hi", language_probability=0.69, all_language_probs=[])
        return iter([SimpleNamespace(words=words)]), info


async def test_hinglish_prefers_confident_roman_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _Hinglish()
    tx = await _transcriber(model, monkeypatch).transcribe("x.wav", "auto")  # type: ignore[arg-type]
    assert model.calls == ["en"]  # confident Roman decode: the slow hi decode is skipped
    assert tx.language == "hinglish" and tx.words[0].lang == "en"


def test_hinglish_fillers_merge_both_lists() -> None:
    from app.analysis.fillers import FillerFinder

    phrases = {" ".join(p) for p in FillerFinder("hinglish").phrases}
    assert {"matlab", "um"} <= phrases
