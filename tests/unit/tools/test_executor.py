"""Executor tests: pure dispatch, actionable failures, no-ops, analysis-driven tools."""

from pathlib import Path

import pytest

from app.analysis.models import FillerHit, SpeechRange
from app.providers.local import AssetCatalog
from app.timeline.schema import Timeline, VideoClip
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
    out = _ok(execute("remove_silence", {}, ToolState(tl, speech=[SpeechRange(start=0, end=1)])))
    assert out.effect == Effect.NONE
    assert "no speech" in out.output(version=1).model_dump()["summary"]


def test_remove_silence_refuses_without_vad_or_when_it_would_gut_the_video() -> None:
    assert _fail(execute("remove_silence", {}, ToolState(_timeline()))).errors[0].code == "no_vad"
    # VAD says only the first 0.5 s is speech but words exist everywhere -> words protect them;
    # a transcript with 1 word and a tiny VAD range would delete > 50%: refuse.
    one_word = _timeline().model_copy(
        update={
            "transcript": Transcript(
                words=[Word(id="w1", start=0.0, end=0.4, text="a", display_text="a", lang="en")]
            )
        }
    )
    fail = _fail(
        execute("remove_silence", {}, ToolState(one_word, speech=[SpeechRange(start=0.0, end=0.5)]))
    )
    assert fail.errors[0].code == "suspicious_silence"


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


def test_every_registered_tool_has_a_handler(tmp_path: Path) -> None:
    from app.tools.mvp import TOOLS

    minimal: dict[str, dict[str, object]] = {
        "trim": {"start_word": "w1", "end_word": "w2"},
        "delete_range": {"start_word": "w1", "end_word": "w2"},
        "cut_by_transcript": {"start_word": "w1", "end_word": "w2"},
        "ask_user": {"question": "?"},
        "search_assets": {"kind": "image", "query": "money"},
        "insert_asset": {"asset_id": "images:1", "at_word": "w5"},
        "insert_meme": {"template_id": "memes:imgflip-1", "top_text": "me", "at_word": "w5"},
        "remove_insert": {"insert_id": "ins9"},
        "add_inserts": {"items": [{"at_word": "w5", "kind": "image", "query": "money"}]},
        "add_music": {"genre": "upbeat"},
        "add_zooms": {"word_ids": ["w6"]},
        "set_transition": {"style": "zoom_in"},
        "add_sfx": {"sfx_type": "pop", "at_word": "w3"},
        "auto_sfx": {},
    }
    vad = [SpeechRange(start=0.0, end=10.0)]
    state = ToolState(_long_timeline(), speech=vad, catalog=_catalog(tmp_path))
    for tool in TOOLS:
        result = execute(tool.name, minimal.get(tool.name, {}), state)
        if tool.name == "remove_insert":  # handled: unknown id is an actionable failure
            assert isinstance(result, ToolFailure) and result.errors[0].code == "unknown_overlay"
            continue
        assert isinstance(result, ToolOutcome), (tool.name, result)


def test_monetised_export_blocks_personal_use_inserts() -> None:
    from app.timeline.schema import Overlay

    meme = Overlay(
        id="ins1",
        type="meme",
        asset="memes:imgflip-1",
        anchor_word="w2",
        duration_ms=1000,
        layout="pip_pop",
        license="unknown",
        commercial_use=False,
    )
    tl = _timeline().model_copy(update={"overlays": [meme]})
    fail = _fail(execute("export", {}, ToolState(tl)))
    assert fail.errors[0].code == "personal_use_assets" and "ins1" in fail.message()
    ok = _ok(execute("export", {"monetized": False}, ToolState(tl)))
    assert ok.effect == Effect.RENDER
    assert _ok(execute("export", {}, ToolState(_timeline()))).effect == Effect.RENDER


def _catalog(tmp_path: Path) -> "AssetCatalog":
    import json

    from PIL import Image

    from app.providers.local import AssetCatalog

    root = tmp_path / "assets"
    (root / "sfx").mkdir(parents=True)
    sfx_items = []
    for t in ("whoosh", "pop", "click", "swoosh", "record_scratch"):
        (root / "sfx" / f"{t}.mp3").write_bytes(b"x")
        sfx_items.append(
            {
                "id": f"sfx:{t}",
                "file": f"{t}.mp3",
                "title": t,
                "tags": [t],
                "source": "test",
                "license": "CC0-1.0",
                "attribution_required": False,
                "commercial_use": True,
                "sfx_type": t,
            }
        )
    (root / "sfx" / "manifest.json").write_text(json.dumps(sfx_items))
    (root / "music").mkdir(parents=True)
    (root / "music" / "a.mp3").write_bytes(b"x")
    (root / "music" / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "id": "music:a",
                    "file": "a.mp3",
                    "title": "Happy Day",
                    "tags": ["upbeat", "happy"],
                    "source": "test",
                    "license": "CC-BY-3.0",
                    "attribution_required": True,
                    "attribution_text": "Happy Day by X, CC BY 3.0",
                    "commercial_use": True,
                    "genre": "upbeat",
                    "duration_s": 120.0,
                }
            ]
        )
    )
    for lib, file, extra in (
        ("images", "1.jpg", {}),
        ("gifs", "1.gif", {}),
        ("memes", "drake.jpg", {"license": "unknown", "commercial_use": False}),
    ):
        (root / lib).mkdir(parents=True)
        Image.new("RGB", (320, 240), "gray").save(
            root / lib / file, "PNG" if lib != "gifs" else "GIF"
        )
        item = {
            "id": f"{lib}:1" if lib != "memes" else "memes:imgflip-1",
            "file": file,
            "title": f"{lib} money",
            "tags": ["money", "cash"],
            "source": "test",
            "license": "CC0-1.0",
            "attribution_required": False,
            "commercial_use": True,
            **extra,
        }
        (root / lib / "manifest.json").write_text(json.dumps([item]))
    return AssetCatalog(root, tmp_path / "memes-out")


def _long_timeline() -> "Timeline":
    from app.timeline.schema import Timeline as TL

    words = [
        Word(
            id=f"w{i + 1}", start=i * 0.5, end=i * 0.5 + 0.4, text="x", display_text="x", lang="en"
        )
        for i in range(20)
    ]
    return TL(
        project_id="00000000-0000-0000-0000-000000000000",  # type: ignore[arg-type]
        transcript=Transcript(words=words),
        video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=10.0)],
    )


def test_search_and_insert_follow_placement_rules(tmp_path: Path) -> None:
    state = ToolState(_long_timeline(), catalog=_catalog(tmp_path))
    hits = _ok(execute("search_assets", {"kind": "image", "query": "money"}, state))
    assert hits.output().model_dump()["results"][0]["id"] == "images:1"
    assert (
        _fail(execute("insert_asset", {"asset_id": "images:1", "at_word": "w1"}, state))
        .errors[0]
        .code
        == "too_early"
    )
    first = _ok(execute("insert_asset", {"asset_id": "gifs:1", "at_word": "w4"}, state))
    assert first.timeline is not None
    ov = first.timeline.overlays[0]
    assert (ov.id, ov.type, ov.layout, ov.license) == ("ins1", "gif", "pip_pop", "CC0-1.0")
    near = ToolState(first.timeline, catalog=state.catalog)
    assert (
        _fail(execute("insert_asset", {"asset_id": "images:1", "at_word": "w6"}, near))
        .errors[0]
        .code
        == "too_close"
    )
    assert (
        _fail(execute("insert_asset", {"asset_id": "images:1", "at_word": "w20"}, near))
        .errors[0]
        .code
        == "past_end"
    )
    second = _ok(execute("insert_asset", {"asset_id": "images:1", "at_word": "w10"}, near))
    assert second.timeline is not None and [o.layout for o in second.timeline.overlays] == [
        "pip_pop",
        "blur_bg_contain",
    ]


def test_insert_meme_renders_caption_and_is_personal_use(tmp_path: Path) -> None:
    state = ToolState(_long_timeline(), catalog=_catalog(tmp_path))
    assert (
        _fail(execute("insert_asset", {"asset_id": "memes:imgflip-1", "at_word": "w5"}, state))
        .errors[0]
        .code
        == "use_insert_meme"
    )
    assert (
        _fail(execute("insert_meme", {"template_id": "memes:imgflip-1", "at_word": "w5"}, state))
        .errors[0]
        .code
        == "empty_meme"
    )
    out = _ok(
        execute(
            "insert_meme",
            {
                "template_id": "memes:imgflip-1",
                "top_text": "cloud bill",
                "bottom_text": "gone",
                "at_word": "w5",
            },
            state,
        )
    )
    assert out.timeline is not None
    ov = out.timeline.overlays[0]
    assert (
        ov.type == "meme" and ov.commercial_use is False and ov.asset.startswith("memes:imgflip-1:")
    )
    assert list((tmp_path / "memes-out").glob("*.png"))
    fail = _fail(execute("export", {}, ToolState(out.timeline)))
    assert fail.errors[0].code == "personal_use_assets"


def test_out_of_range_numbers_are_clamped_not_rejected() -> None:
    out = _ok(
        execute(
            "remove_silence",
            {"min_silence_ms": 100, "padding_ms": 900},
            ToolState(_timeline(), speech=[SpeechRange(start=0.0, end=3.0)]),
        )
    )
    assert out.tool.name == "remove_silence"
    fail = _fail(execute("remove_silence", {"min_silence_ms": "lots"}, ToolState(_timeline())))
    assert fail.errors[0].field == "min_silence_ms"


def test_add_inserts_places_matches_and_skips_with_reasons(tmp_path: Path) -> None:
    state = ToolState(_long_timeline(), catalog=_catalog(tmp_path))
    plan = {
        "items": [
            {"at_word": "w4", "kind": "gif", "query": "money"},
            {"at_word": "w6", "kind": "image", "query": "money"},  # too close to w4
            {
                "at_word": "w10",
                "kind": "meme",
                "query": "money",
                "top_text": "me",
                "bottom_text": "rich",
            },
            {"at_word": "w14", "kind": "image", "query": "submarine"},  # no match
        ]
    }
    out = _ok(execute("add_inserts", plan, state))
    data = out.output(version=2).model_dump()
    assert [p["at_word"] for p in data["placed"]] == ["w4", "w10"]
    assert {s["at_word"]: s["reason"] for s in data["skipped"]} == {
        "w6": "too_close",
        "w14": "no unused image matches 'submarine'",
    }
    assert out.timeline is not None and [o.type for o in out.timeline.overlays] == ["gif", "meme"]


def test_add_music_sets_licensed_bed_and_credit(tmp_path: Path) -> None:
    from app.workers.render import attributions

    state = ToolState(_long_timeline(), catalog=_catalog(tmp_path))
    out = _ok(execute("add_music", {"genre": "upbeat", "mood": "happy party"}, state))
    assert out.timeline is not None and out.timeline.music is not None
    assert out.timeline.music.asset == "music:a" and out.timeline.music.level_db == -20.0
    assert out.output(version=2).model_dump()["track"] == "Happy Day"
    assert attributions(out.timeline) == ["Happy Day by X, CC BY 3.0"]
    gone = _ok(execute("remove_music", {}, ToolState(out.timeline, catalog=state.catalog)))
    assert gone.timeline is not None and gone.timeline.music is None


def test_add_inserts_never_repeats_an_asset(tmp_path: Path) -> None:
    state = ToolState(_long_timeline(), catalog=_catalog(tmp_path))
    plan = {
        "items": [
            {"at_word": "w4", "kind": "image", "query": "money"},
            {"at_word": "w12", "kind": "image", "query": "money"},  # only one image exists
        ]
    }
    data = _ok(execute("add_inserts", plan, state)).output(version=2).model_dump()
    assert [p["at_word"] for p in data["placed"]] == ["w4"]
    assert data["skipped"] == [{"at_word": "w12", "reason": "no unused image matches 'money'"}]


def test_add_zooms_enforces_pacing_rules() -> None:
    state = ToolState(_long_timeline())  # 20 words over 10 s -> cap of 2 zooms
    out = _ok(execute("add_zooms", {"word_ids": ["w3", "w5", "w12", "w18", "w99"]}, state))
    data = out.output(version=2).model_dump()
    assert data["placed"] == ["w3", "w12"]
    reasons = {s["word"]: s["reason"] for s in data["skipped"]}
    assert reasons["w5"].startswith("within 2.5s") and reasons["w18"].startswith("density cap")
    assert reasons["w99"] == "word not in the video"
    assert out.timeline is not None and out.timeline.zooms[0].hold_ms == 900


def test_set_transition_style() -> None:
    out = _ok(execute("set_transition", {"style": "whip"}, ToolState(_timeline())))
    assert out.timeline is not None and out.timeline.transition == "whip"
    assert _fail(execute("set_transition", {"style": "spin"}, ToolState(_timeline()))).errors


def test_auto_sfx_syncs_to_cuts_inserts_and_zooms(tmp_path: Path) -> None:
    from app.timeline import ops as tops

    tl, _ = tops.delete_words(_long_timeline(), ["w9"])  # one cut at ~4.0 s
    state = ToolState(tl, catalog=_catalog(tmp_path))
    tl = _ok(
        execute(
            "add_inserts", {"items": [{"at_word": "w4", "kind": "gif", "query": "money"}]}, state
        )
    ).timeline
    assert tl is not None
    tl = _ok(execute("add_zooms", {"word_ids": ["w14"]}, ToolState(tl))).timeline
    assert tl is not None
    out = _ok(execute("auto_sfx", {}, ToolState(tl, catalog=state.catalog)))
    placed = out.output(version=5).model_dump()["placed"]
    assert [p.split("@")[0] for p in placed] == ["pop", "whoosh", "click"]
    assert out.timeline is not None and all(h.id.startswith("auto") for h in out.timeline.sfx)
    again = _ok(execute("auto_sfx", {}, ToolState(out.timeline, catalog=state.catalog)))
    assert again.timeline is not None and len(again.timeline.sfx) == 3  # idempotent
