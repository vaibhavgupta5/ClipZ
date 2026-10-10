"""generate_sfx / add_voiceover / dub_video: placement rules, licensing, fallbacks."""

from app.providers.local import LibraryItem
from app.timeline.transcript import Transcript
from app.tools.executor import (
    Effect,
    ToolFailure,
    ToolOutcome,
    ToolState,
    execute,
    personal_use_assets,
)
from tests.unit.timeline.test_ops import _timeline


def _item(duration_s: float = 1.0, commercial: bool = False) -> LibraryItem:
    return LibraryItem(
        id="generated:0123456789abcdef",
        file="x.mp3",
        title="t",
        tags=["sfx"],
        source="elevenlabs",
        license="ElevenLabs-free-plan",
        attribution_required=not commercial,
        attribution_text="" if commercial else "Audio generated with ElevenLabs",
        commercial_use=commercial,
        duration_s=duration_s,
    )


def _ok(res: ToolOutcome | ToolFailure) -> ToolOutcome:
    assert isinstance(res, ToolOutcome), res
    return res


def test_generate_sfx_places_prepared_clip() -> None:
    state = ToolState(_timeline(), prepared=_item())
    out = _ok(execute("generate_sfx", {"prompt": "crowd gasp", "at_word": "w3"}, state))
    assert out.timeline is not None
    hit = out.timeline.sfx[0]
    assert hit.id == "gen1" and hit.asset == "generated:0123456789abcdef"
    assert hit.max_ms == 1000 and not hit.commercial_use
    assert hit.attribution_text  # free plan: credited
    assert personal_use_assets(out.timeline) == ["gen1"]


def test_generate_sfx_without_elevenlabs_points_to_library() -> None:
    res = execute("generate_sfx", {"prompt": "crowd gasp", "at_word": "w3"}, ToolState(_timeline()))
    assert isinstance(res, ToolFailure)
    assert res.errors[0].code == "generation_unavailable" and "add_sfx" in res.message()


def test_voiceover_is_placed_and_must_fit() -> None:
    state = ToolState(_timeline(), prepared=_item(1.0, commercial=True))
    out = _ok(execute("add_voiceover", {"text": "Here is the trick", "at_word": "w1"}, state))
    assert out.timeline is not None
    vo = out.timeline.voiceovers[0]
    assert vo.id == "vo1" and vo.duration_ms == 1000 and vo.duck_db == -14.0
    # w5 ends at 2.4 s; a 1 s clip would run past the 3 s end
    late = execute("add_voiceover", {"text": "late", "at_word": "w5"}, state)
    assert isinstance(late, ToolFailure) and late.errors[0].code == "voiceover_too_long"


def test_voiceovers_cannot_overlap() -> None:
    state = ToolState(_timeline(), prepared=_item(1.0))
    tl = _ok(execute("add_voiceover", {"text": "one", "at_word": "w1"}, state)).timeline
    assert tl is not None
    res = execute(
        "add_voiceover", {"text": "two", "at_word": "w2"}, ToolState(tl, prepared=_item())
    )
    assert isinstance(res, ToolFailure) and res.errors[0].code == "voiceover_overlap"
    removed = _ok(execute("remove_voiceover", {"voiceover_id": "vo1"}, ToolState(tl)))
    assert removed.timeline is not None and removed.timeline.voiceovers == []


def test_dub_video_queues_a_job_and_rejects_same_language() -> None:
    tl = _timeline()
    out = _ok(execute("dub_video", {"target_language": "hi"}, ToolState(tl)))
    assert out.effect == Effect.DUB and out.fields["language"] == "hi"
    en = tl.model_copy(update={"transcript": Transcript(words=tl.transcript.words, language="en")})
    same = execute("dub_video", {"target_language": "en"}, ToolState(en))
    assert isinstance(same, ToolFailure) and same.errors[0].code == "same_language"
    none = execute("dub_video", {"target_language": "original"}, ToolState(tl))
    assert isinstance(none, ToolFailure) and none.errors[0].code == "no_dub"
