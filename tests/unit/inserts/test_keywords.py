"""Keyword engine: spoken words -> relevant visuals, placed briefly and in sync."""

import json
from pathlib import Path

from app.inserts.keywords import find_cues, plan_cues, spoken_terms
from app.providers.local import AssetCatalog
from app.timeline import snap
from app.timeline.schema import Timeline, VideoClip
from app.timeline.transcript import Transcript, Word
from app.tools.executor import ToolOutcome, ToolState, execute


def _tl(text: str, reframe: str = "center") -> Timeline:
    words = [
        Word(id=f"w{i + 1}", start=i * 0.5, end=i * 0.5 + 0.4, text=t, display_text=t, lang="en")
        for i, t in enumerate(text.split())
    ]
    return Timeline(
        project_id="00000000-0000-0000-0000-000000000000",  # type: ignore[arg-type]
        transcript=Transcript(words=words),
        video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=len(words) * 0.5)],
        reframe=reframe,  # type: ignore[arg-type]
    )


def _lib(root: Path, name: str, items: list[dict[str, object]]) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    for item in items:
        (root / name / str(item["file"])).write_bytes(b"x")
    (root / name / "manifest.json").write_text(json.dumps(items))


def _item(lib: str, key: str, tags: list[str], **extra: object) -> dict[str, object]:
    return {
        "id": f"{lib}:{key}",
        "file": f"{key}.png",
        "title": key,
        "tags": tags,
        "source": "test",
        "license": "CC0-1.0",
        "attribution_required": False,
        "commercial_use": True,
        **extra,
    }


def _catalog(tmp_path: Path) -> AssetCatalog:
    root = tmp_path / "assets"
    _lib(
        root,
        "icons",
        [
            _item("icons", "openai", ["openai", "chatgpt"], aliases=["chatgpt", "openai", "gpt"]),
            _item("icons", "react", ["react"], aliases=["react"]),
        ],
    )
    _lib(
        root,
        "images",
        [
            _item("images", "server1", ["server", "datacenter"]),
            _item("images", "server2", ["server", "rack"]),
            _item("images", "cash", ["money", "cash"]),
            _item("images", "generic", ["technology", "video", "stock"]),
        ],
    )
    return AssetCatalog(root, tmp_path / "memes")


TEXT = (
    "so today I opened ChatGPT and asked about our server costs because the money "
    "we spend on every server is insane and people react badly"
)


def test_brand_alias_and_tags_match_spoken_words(tmp_path: Path) -> None:
    cues = find_cues(_tl(TEXT), _catalog(tmp_path))
    by_phrase = {(c.phrase, c.asset_id) for c in cues}
    assert ("chatgpt", "icons:openai") in by_phrase
    assert ("money", "images:cash") in by_phrase
    assert any(c.phrase == "server" and c.asset_id.startswith("images:server") for c in cues)
    # lowercase "react" is the verb, not the framework
    assert not any(c.asset_id == "icons:react" for c in cues)
    # stopwords and generic medium words never become cues
    assert not any(c.phrase in ("today", "video", "people") for c in cues)


def test_repeated_word_falls_back_to_unused_asset(tmp_path: Path) -> None:
    tl = _tl(TEXT)
    cues = [c for c in find_cues(tl, _catalog(tmp_path)) if c.phrase == "server"]
    plan = plan_cues(cues, tl.duration_s, gap_s=2.0, per_s=1.0, hook_s=0.0)
    assert sorted(c.asset_id for c in plan) == ["images:server1", "images:server2"]


def test_cut_words_are_not_cues(tmp_path: Path) -> None:
    tl = _tl(TEXT)
    chat = tl.transcript.word_index["w5"]  # "ChatGPT"
    tl = tl.model_copy(
        update={
            "video_clips": [
                VideoClip(id="c1", asset="a1", src_in=0.0, src_out=chat.start),
                VideoClip(id="c2", asset="a1", src_in=chat.end + 0.05, src_out=tl.duration_s),
            ]
        }
    )
    assert "chatgpt" not in {t.text for t in spoken_terms(tl)}


def test_auto_visuals_places_short_synced_inserts(tmp_path: Path) -> None:
    out = execute(
        "auto_visuals", {"density": "dense"}, ToolState(_tl(TEXT), catalog=_catalog(tmp_path))
    )
    assert isinstance(out, ToolOutcome) and out.timeline is not None
    tl = out.timeline
    assert {o.asset for o in tl.overlays} >= {"icons:openai", "images:cash"}
    for o in tl.overlays:
        assert o.duration_ms <= {"icon": 1500, "image": 2200}[o.type]
    assert len({o.asset for o in tl.overlays}) == len(tl.overlays)  # never repeated


def test_split_screen_chains_without_stretching(tmp_path: Path) -> None:
    state = ToolState(_tl(TEXT, reframe="split_bottom"), catalog=_catalog(tmp_path))
    out = execute("auto_visuals", {}, state)
    assert isinstance(out, ToolOutcome) and out.timeline is not None
    windows = sorted(snap.overlay_window(out.timeline, o) for o in out.timeline.overlays)
    assert all(o.layout == "split_top" for o in out.timeline.overlays)
    assert all(e - s <= 2.2 + 1e-6 for s, e in windows)  # capped, never stretched to fill
    assert all(a[1] <= b[0] + 1e-6 for a, b in zip(windows, windows[1:], strict=False))


def test_add_inserts_without_query_uses_what_is_said(tmp_path: Path) -> None:
    state = ToolState(_tl(TEXT), catalog=_catalog(tmp_path))
    out = execute("add_inserts", {"items": [{"at_word": "w5", "kind": "icon"}]}, state)
    assert isinstance(out, ToolOutcome) and out.timeline is not None
    assert out.timeline.overlays[0].asset == "icons:openai"
    assert out.timeline.overlays[0].type == "icon"
