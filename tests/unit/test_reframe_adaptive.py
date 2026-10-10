"""Unit tests for adaptive reframing and tech icon detection."""

import uuid

from app.inserts.keywords import _keep
from app.render.edl_to_ffmpeg import FINAL, build_video_filter
from app.timeline.schema import CaptionConfig, Overlay, Timeline, VideoClip
from app.timeline.transcript import Transcript, Word
from app.tools import mvp
from app.tools.executor import Effect, ToolState, execute


def _make_timeline(
    words_list: list[str], overlays: list[Overlay] | None = None, reframe: str = "adaptive"
) -> Timeline:
    words = [
        Word(
            id=f"w{i + 1}",
            start=float(i * 0.5),
            end=float((i + 1) * 0.5),
            text=w,
            display_text=w,
            lang="en",
        )
        for i, w in enumerate(words_list)
    ]
    clips = [VideoClip(id="c1", asset="master", src_in=0.0, src_out=len(words) * 0.5)]
    return Timeline(
        project_id=uuid.uuid4(),
        version=1,
        transcript=Transcript(words=words, language="en"),
        video_clips=clips,
        captions=CaptionConfig(),
        overlays=overlays or [],
        reframe=reframe,  # type: ignore[arg-type]
    )


def test_tech_always_keywords_kept_even_if_lowercase():
    assert _keep("reactjs", "reactjs") is True
    assert _keep("react", "React") is True
    assert _keep("python", "python") is True
    assert _keep("docker", "docker") is True
    assert _keep("chatgpt", "chatgpt") is True


def test_adaptive_reframe_switches_to_blur_when_no_overlay():
    tl = _make_timeline(["building", "a", "cool", "app"], overlays=[], reframe="adaptive")
    script, out_label = build_video_filter(tl, FINAL)
    # When no overlay is present, adaptive reframe compiles to ReframeBlur
    assert "boxblur=20" in script
    assert "drawbox=" not in script


def test_adaptive_reframe_switches_to_split_bottom_when_split_top_overlay_present():
    overlay = Overlay(
        id="ins1",
        type="icon",
        asset="icons:react",
        anchor_word="w2",
        offset_ms=0,
        duration_ms=1200,
        layout="split_top",
        license="MIT",
    )
    tl = _make_timeline(
        ["building", "with", "react", "today"], overlays=[overlay], reframe="adaptive"
    )
    asset_labels = {"ins1": "[1:v]"}
    script, out_label = build_video_filter(tl, FINAL, asset_labels=asset_labels)
    # When split_top overlay is present, adaptive reframe compiles to ReframeSplitBottom
    assert "drawbox=" in script
    assert "0x0d0e15@1.0" in script


def test_icon_in_split_top_is_properly_scaled_and_centered():
    overlay = Overlay(
        id="ins1",
        type="icon",
        asset="icons:react",
        anchor_word="w1",
        offset_ms=0,
        duration_ms=1000,
        layout="split_top",
        license="MIT",
    )
    tl = _make_timeline(["react", "framework"], overlays=[overlay], reframe="split_bottom")
    asset_labels = {"ins1": "[1:v]"}
    script, out_label = build_video_filter(tl, FINAL, asset_labels=asset_labels)
    # Contained logo tile centered in top panel
    assert "scale=" in script
    assert "overlay=" in script
    assert "(W-w)/2" in script


def test_reframe_tool_accepts_adaptive():
    tl = _make_timeline(["hello", "world"], reframe="center")
    inp = mvp.ReframeIn(mode="adaptive")
    state = ToolState(timeline=tl)
    outcome = execute("reframe_9x16", inp.model_dump(), state)
    assert outcome.effect == Effect.NEW_VERSION
    assert outcome.timeline.reframe == "adaptive"


def test_split_segments_adaptive_slices_clip_at_overlay_boundaries():
    from app.render.segments import split_segments

    # 30 words (each 1.0s) = 30.0s total.
    # Overlay anchored on w10 (start 9.0s) for 3.0s (ends at 12.0s).
    overlay = Overlay(
        id="ins1",
        type="video",
        asset="videos:matrix-code-rain",
        anchor_word="w10",
        offset_ms=0,
        duration_ms=3000,
        layout="split_top",
        license="MIT",
    )
    words = [f"w{i + 1}" for i in range(30)]
    # Use 1.0s words
    w_objs = [
        Word(id=f"w{i + 1}", start=float(i), end=float(i + 1), text=w, display_text=w, lang="en")
        for i, w in enumerate(words)
    ]
    clips = [VideoClip(id="c1", asset="master", src_in=0.0, src_out=30.0)]
    tl = Timeline(
        project_id=uuid.uuid4(),
        version=1,
        transcript=Transcript(words=w_objs, language="en"),
        video_clips=clips,
        captions=CaptionConfig(),
        overlays=[overlay],
        reframe="adaptive",
    )
    segs = split_segments(tl)
    # Clip is sliced into settled subsegments (>= 6s hold):
    # 1. 0.0s -> 9.0s (Middle Blur, 9s)
    # 2. 9.0s -> 15.0s (Split Bottom, 6s min hold)
    # 3. 15.0s -> 30.0s (Middle Blur, 15s)
    assert len(segs) == 3
    assert segs[0].start == 0.0 and segs[0].end == 9.0
    assert segs[0].is_split_layout is False
    assert segs[1].start == 9.0 and segs[1].end == 15.0
    assert segs[1].is_split_layout is True
    assert segs[2].start == 15.0 and segs[2].end == 30.0
    assert segs[2].is_split_layout is False


def test_adaptive_smoothing_bridges_nearby_overlays_to_avoid_flicker():
    from app.render.segments import split_segments

    # 40s timeline with 2 overlays with only a 2s gap between them
    # Overlay 1: 10.0s -> 13.0s
    # Overlay 2: 15.0s -> 18.0s
    o1 = Overlay(
        id="ins1",
        type="video",
        asset="videos:matrix-code-rain",
        anchor_word="w11",
        offset_ms=0,
        duration_ms=3000,
        layout="split_top",
        license="MIT",
    )
    o2 = Overlay(
        id="ins2",
        type="image",
        asset="images:test-image",
        anchor_word="w16",
        offset_ms=0,
        duration_ms=3000,
        layout="split_top",
        license="MIT",
    )
    w_objs = [
        Word(
            id=f"w{i + 1}",
            start=float(i),
            end=float(i + 1),
            text=f"w{i + 1}",
            display_text=f"w{i + 1}",
            lang="en",
        )
        for i in range(40)
    ]
    clips = [VideoClip(id="c1", asset="master", src_in=0.0, src_out=40.0)]
    tl = Timeline(
        project_id=uuid.uuid4(),
        version=1,
        transcript=Transcript(words=w_objs, language="en"),
        video_clips=clips,
        captions=CaptionConfig(),
        overlays=[o1, o2],
        reframe="adaptive",
    )
    segs = split_segments(tl)
    # The 2s gap between o1 and o2 is bridged so the layout does NOT flicker to middle blur for 2s!
    # Segments:
    # 1. 0.0s -> 10.0s: Middle Blur (10s)
    # 2. 10.0s -> 21.0s: Split Screen (11s continuous bridged split)
    # 3. 21.0s -> 40.0s: Middle Blur (19s)
    assert len(segs) == 3
    assert segs[0].is_split_layout is False
    assert segs[1].is_split_layout is True
    assert segs[1].start == 10.0 and segs[1].end >= 21.0
    assert segs[2].is_split_layout is False


def test_blur_reframe_forces_captions_to_bottom():
    from app.render.captions_ass import ass_for_cues
    from app.render.segments import split_segments

    # Timeline with user requesting middle captions (position="center")
    overlay = Overlay(
        id="ins1",
        type="video",
        asset="videos:matrix-code-rain",
        anchor_word="w10",
        offset_ms=0,
        duration_ms=3000,
        layout="split_top",
        license="MIT",
    )
    words = [f"w{i + 1}" for i in range(25)]
    w_objs = [
        Word(id=f"w{i + 1}", start=float(i), end=float(i + 1), text=w, display_text=w, lang="en")
        for i, w in enumerate(words)
    ]
    clips = [VideoClip(id="c1", asset="master", src_in=0.0, src_out=25.0)]
    tl = Timeline(
        project_id=uuid.uuid4(),
        version=1,
        transcript=Transcript(words=w_objs, language="en"),
        video_clips=clips,
        captions=CaptionConfig(position="center"),  # user asked for center
        overlays=[overlay],
        reframe="adaptive",
    )

    segs = split_segments(tl)
    # Seg 0: Middle Blur view (0.0 to 9.0s) -> caption_pos must be "center_lower" (alignment 2)
    seg0 = segs[0]
    seg0_reframe = "split_bottom" if seg0.is_split_layout else "blur"
    assert seg0_reframe == "blur"
    caption_pos_seg0 = "center_lower" if seg0_reframe == "blur" else tl.captions.position
    assert caption_pos_seg0 == "center_lower"
    ass0 = ass_for_cues(seg0.cues, position=caption_pos_seg0)
    assert ",2,60,60,340,1" in ass0  # Alignment 2 = bottom-center

    # Seg 1: Split Screen view (9.0 to 15.0s) -> caption_pos preserves user "center" (alignment 5)
    seg1 = segs[1]
    seg1_reframe = "split_bottom" if seg1.is_split_layout else "blur"
    assert seg1_reframe == "split_bottom"
    caption_pos_seg1 = "center_lower" if seg1_reframe == "blur" else tl.captions.position
    assert caption_pos_seg1 == "center"
    ass1 = ass_for_cues(seg1.cues, position=caption_pos_seg1)
    assert ",5,60,60,340,1" in ass1  # Alignment 5 = middle-center


def _clips_with_skips(skips: list[float], clip_s: float = 5.0) -> list[VideoClip]:
    """Back-to-back clips of ``clip_s`` seconds, each next one skipping ``skips[i]`` of source."""
    clips, t = [VideoClip(id="c0", asset="master", src_in=0.0, src_out=clip_s)], clip_s
    for n, skip in enumerate(skips, start=1):
        clips.append(VideoClip(id=f"c{n}", asset="master", src_in=t + skip, src_out=t + skip + 5))
        t += skip + clip_s
    return clips


def test_only_real_cuts_are_styled_after_adaptive_slicing():
    from app.render.segments import split_segments

    words = [
        Word(
            id=f"w{i + 1}", start=i * 0.5, end=i * 0.5 + 0.4, text="x", display_text="x", lang="en"
        )
        for i in range(60)
    ]
    # c1 and c2 are breath trims (0.2 s skipped), c3 skips 2 s of source: one real cut
    clips = _clips_with_skips([0.2, 0.2, 2.0])
    panel = Overlay(
        id="ins1",
        type="image",
        asset="images:x",
        anchor_word="w12",  # 5.5 s: slices c1 into split + blur parts
        offset_ms=0,
        duration_ms=1500,
        layout="split_top",
        license="CC0-1.0",
    )
    tl = Timeline(
        project_id=uuid.uuid4(),
        transcript=Transcript(words=words, language="en"),
        video_clips=clips,
        overlays=[panel],
        transition="zoom_in",
        reframe="adaptive",
    )
    segs = split_segments(tl)
    assert len(segs) > len(clips)  # adaptive layout switches add slices
    styled = [s for s in segs if s.is_real_cut]
    assert len(styled) == 1 and styled[0].clips[0].src_in == clips[3].src_in


def test_adaptive_visuals_go_to_the_top_panel_by_default(tmp_path):
    from app.render.segments import split_segments
    from tests.unit.inserts.test_keywords import TEXT, _catalog, _tl

    catalog = _catalog(tmp_path)
    state = ToolState(_tl(f"{TEXT} {TEXT} {TEXT}", reframe="adaptive"), catalog=catalog)
    out = execute("auto_visuals", {}, state)
    assert out.effect == Effect.NEW_VERSION
    assert out.timeline.overlays and all(o.layout == "split_top" for o in out.timeline.overlays)
    assert all(o.offset_ms == 0 for o in out.timeline.overlays)  # on the word as it is said
    layouts = {s.is_split_layout for s in split_segments(out.timeline)}
    assert layouts == {True, False}  # split while a visual shows, blur view otherwise

    stickers = execute("auto_visuals", {"layout": "pip_pop"}, state)
    assert all(o.layout == "pip_pop" for o in stickers.timeline.overlays)  # explicit wins
    assert not any(s.is_split_layout for s in split_segments(stickers.timeline))


def test_adaptive_insert_asset_defaults_to_the_top_panel(tmp_path):
    from tests.unit.inserts.test_keywords import TEXT, _catalog, _tl

    state = ToolState(_tl(TEXT, reframe="adaptive"), catalog=_catalog(tmp_path))
    out = execute("insert_asset", {"asset_id": "images:cash", "at_word": "w8"}, state)
    assert out.timeline.overlays[0].layout == "split_top"


def test_explicit_segment_reframe_wins_over_overlay_inference():
    tl = _make_timeline(["a", "b", "c", "d"], reframe="adaptive")
    split, _ = build_video_filter(tl, FINAL, reframe="split_bottom", overlay_windows=[])
    blur, _ = build_video_filter(tl, FINAL, reframe="blur", overlay_windows=[])
    assert "drawbox=" in split and "drawbox=" not in blur and "boxblur=20" in blur


def test_split_top_meme_hides_captions_from_the_word_start():
    from app.render.segments import split_segments

    meme = Overlay(
        id="m1",
        type="meme",
        asset="memes:x:0123456789abcdef",
        anchor_word="w3",  # said 1.0-1.5 s; a panel meme shows from 1.0 s
        offset_ms=0,
        duration_ms=1200,
        layout="split_top",
        license="unknown",
        commercial_use=False,
    )
    tl = _make_timeline([f"t{i}" for i in range(8)], overlays=[meme], reframe="split_bottom")
    cues = [c.id for seg in split_segments(tl) for c in seg.cues]
    # hidden 1.0-2.2 s (w3, w4, w5), back afterwards although the panel meme stays up
    assert cues == ["w1", "w2", "w6", "w7", "w8"]
