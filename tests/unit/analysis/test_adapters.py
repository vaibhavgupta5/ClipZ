"""Adapter tests: missing heavy deps -> ModelUnavailable with install hints (no import at load)."""

import sys

import pytest

from app.analysis.adapters import ModelUnavailable


def test_whisper_adapter_unavailable_without_dep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setitem(sys.modules, "faster_whisper.WhisperModel", None)
    from app.analysis.asr_whisper import FasterWhisperTranscriber

    with pytest.raises(ModelUnavailable, match="faster-whisper"):
        FasterWhisperTranscriber()


def test_silero_adapter_unavailable_without_dep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)
    from app.analysis.vad_silero import SileroVad

    with pytest.raises(ModelUnavailable, match="torch"):
        SileroVad()


def test_mediapipe_adapter_unavailable_without_dep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mediapipe", None)
    from app.analysis.faces_mediapipe import MediaPipeFaces

    with pytest.raises(ModelUnavailable, match="mediapipe"):
        MediaPipeFaces()


def test_adapters_do_not_import_heavy_deps_at_module_load() -> None:
    for mod in ("torch", "faster_whisper", "mediapipe", "librosa", "cv2"):
        assert mod not in sys.modules, f"{mod} imported at test-module load"
