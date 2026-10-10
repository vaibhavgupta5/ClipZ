"""Executor tests: pure dispatch, actionable failures, no-ops, analysis-driven tools."""

import pytest

from app.analysis.models import FillerHit, SpeechRange
from app.timeline.schema import VideoClip
from app.timeline.transcript import Transcript, Word
from app.tools.executor import Effect, ToolFailure, ToolOutcome, ToolState, execute
from tests.unit.timeline.test_ops import _timeline


def _ok(result: ToolOutcome | ToolFailure) -> ToolOutcome:
    assert isinstance(result, ToolOutcome), getattr(result, "errors", result)
    return result


def _fail(result: ToolOutcome | ToolFailure) -> ToolFailure:
    assert isinstance(result, ToolFailure), result
    return result


def test_get_timeline_is_read_only() -> None:
    out = _ok(execute("get_timeline", {}, ToolState(_timeline())))
    assert out.effect == Effect.NONE and out.timeline is None
    model = out.output(version=3)
    assert model.model_dump()["duration_s"] == pytest.approx(3.0)


def test_trim_returns_new_timeline_and_output() -> None:
    out = _ok(
        execute("trim", {"start_word": "w2", "end_word": "w3", "pad_ms": 0}, ToolState(_timeline()))
    )
    assert out.effect == Effect.NEW_VERSION and out.needs_preview
    assert out.timeline is not None and out.timeline.duration_s == pytest.approx(0.9)
    assert out.output(version=2).model_dump()["op"] == "trim"


def test_unknown_tool_and_bad_args_are_actionable() -> None:
    fail = _fail(execute("trimm", {}, ToolState(_timeline())))
    assert fail.errors[0].code == "unknown_tool" and "trim" in fail.message()
    fail = _fail(execute("trim", {"start_word": "12"}, ToolState(_timeline())))
    fields = {e.field for e in fail.errors}
    assert {"start_word", "end_word"} <= fields


def test_scalar_strings_are_coerced() -> None:
    out = _ok(
        execute(
            "trim", {"start_word": "w1", "end_word": "w2", "pad_ms": "50"}, ToolState(_timeline())
        )
    )
    assert out.timeline is not None


def test_op_errors_become_failures() -> None:
    fail = _fail(
        execute("delete_range", {"start_word": "w9", "end_word": "w9"}, ToolState(_timeline()))
    )
    assert fail.errors[0].code == "unknown_word"
    fail = _fail(
        execute("delete_range", {"start_word": "w1", "end_word": "w6"}, ToolState(_timeline()))
    )
    assert fail.errors[0].code == "empty_timeline"


def test_remove_silence_cuts_only_long_word_free_vad_gaps() -> None:
    # words every 0.5 s with 0.1 s gaps; VAD says 0.0-1.4 speech, then silence to 2.5
    tl = _timeline()
    state = ToolState(tl, speech=[SpeechRange(start=0.0, end=1.4), SpeechRange(start=2.5, end=3.0)])
    # word w4 sits at 1.5-1.9 and w5 at 2.0-2.4 so VAD-silence overlaps words: nothing long enough
    out = _ok(execute("remove_silence", {"min_silence_ms": 300, "padding_ms": 0}, state))
    assert out.effect == Effect.NONE
    assert out.output(version=1).model_dump()["removed_ms"] == 0


def test_remove_silence_removes_long_gap() -> None:
    words = [
        Word(id="w1", start=0.0, end=0.4, text="a", display_text="a", lang="en"),
        Word(id="w2", start=1.4, end=1.8, text="b", display_text="b", lang="en"),
    ]
    tl = _timeline().model_copy(
        update={
            "transcript": Transcript(words=words),
            "video_clips": [VideoClip(id="c1", asset="a1", src_in=0.0, src_out=2.0)],
        }
    )
    state = ToolState(
        tl, speech=[SpeechRange(start=0.0, end=0.45), SpeechRange(start=1.35, end=1.85)]
    )
    out = _ok(execute("remove_silence", {"min_silence_ms": 500, "padding_ms": 100}, state))
    assert out.effect == Effect.NEW_VERSION
    fields = out.output(version=2).model_dump()
    assert fields["removed_ms"] == 700  # 0.45..1.35 gap minus 2x100 ms padding
    assert fields["ranges"] == 1
    assert out.timeline is not None and out.timeline.duration_s == pytest.approx(1.3)


def test_remove_silence_without_words_is_noop() -> None:
    tl = _timeline().model_copy(update={"transcript": Transcript(words=[])})
    out = _ok(execute("remove_silence", {}, ToolState(tl)))
    assert out.effect == Effect.NONE
    assert "no speech" in out.output(version=1).model_dump()["summary"]


def test_remove_fillers_uses_analysis_hits_and_lang() -> None:
    state = ToolState(_timeline(), fillers=[FillerHit(word_id="w1", filler="a")])
    out = _ok(execute("remove_fillers", {}, state))
    assert out.output(version=2).model_dump()["removed"] == ["w1"]
    out = _ok(execute("remove_fillers", {"lang": "hi"}, state))
    assert out.effect == Effect.NONE
    fail = _fail(execute("remove_fillers", {"lang": "fr"}, state))
    assert fail.errors[0].code == "bad_lang"


def test_add_captions_sets_all_and_style_patches_some() -> None:
    out = _ok(
        execute(
            "add_captions",
            {"highlight_color": "#FFFF00", "words_per_line": 1},
            ToolState(_timeline()),
        )
    )
    assert out.timeline is not None
    styled = out.timeline
    assert styled.captions.highlight_color == "#FFFF00" and styled.captions.words_per_line == 1
    out = _ok(execute("set_caption_style", {"size_px": 110}, ToolState(styled)))
    assert out.timeline is not None
    assert out.timeline.captions.size_px == 110
    assert out.timeline.captions.highlight_color == "#FFFF00"  # untouched
    fail = _fail(execute("set_caption_style", {"base_color": "red;evil"}, ToolState(styled)))
    assert fail.errors[0].field == "base_color"


def test_audio_fx_tools_are_idempotent() -> None:
    out = _ok(execute("denoise", {}, ToolState(_timeline())))
    assert out.timeline is not None
    again = _ok(execute("denoise", {}, ToolState(out.timeline)))
    assert again.timeline is not None
    assert [e.type for e in again.timeline.audio_fx] == ["denoise"]
    loud = _ok(execute("normalize_loudness", {"target_lufs": -16}, ToolState(again.timeline)))
    assert loud.timeline is not None
    assert loud.timeline.audio_fx[-1].params == {"lufs": -16.0}
    off = _ok(execute("denoise", {"enabled": False}, ToolState(loud.timeline)))
    assert off.timeline is not None
    assert [e.type for e in off.timeline.audio_fx] == ["loudnorm"]


def test_reframe_render_ask_undo_effects() -> None:
    state = ToolState(_timeline())
    assert _ok(execute("reframe_9x16", {"mode": "blur"}, state)).effect == Effect.NEW_VERSION
    assert _ok(execute("preview", {}, state)).effect == Effect.RENDER
    assert _fail(execute("preview", {"start_word": "w1"}, state)).errors[0].code == "half_range"
    export = _ok(execute("export", {}, state))
    assert export.output(render_id="r", job_id="j").model_dump()["profile"] == "final"
    ask = _ok(execute("ask_user", {"question": "Which part?", "options": ["a", "b"]}, state))
    assert ask.effect == Effect.ASK
    assert _ok(execute("undo", {}, state)).effect == Effect.UNDO
    assert _ok(execute("redo", {}, state)).effect == Effect.REDO


def test_every_registered_tool_has_a_handler() -> None:
    from app.tools.mvp import TOOLS

    minimal: dict[str, dict[str, object]] = {
        "trim": {"start_word": "w1", "end_word": "w2"},
        "delete_range": {"start_word": "w1", "end_word": "w2"},
        "cut_by_transcript": {"start_word": "w1", "end_word": "w2"},
        "ask_user": {"question": "?"},
    }
    for tool in TOOLS:
        result = execute(tool.name, minimal.get(tool.name, {}), ToolState(_timeline()))
        assert isinstance(result, ToolOutcome), (tool.name, result)
