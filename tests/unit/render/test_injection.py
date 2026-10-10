"""Render injection fuzz: hostile text must render as glyphs, never as control syntax."""

import asyncio
import os
import tempfile
import uuid
from pathlib import Path

import pytest

from app.media.probe import probe_file
from app.render.captions_ass import CaptionCue, ass_for_cues, escape_ass
from app.render.edl_to_ffmpeg import PREVIEW
from app.render.render import render_timeline
from app.settings import get_settings
from app.storage import get_storage
from app.timeline.schema import CaptionConfig, OutputSpec, Timeline, VideoClip
from app.timeline.transcript import Transcript, Word

PAYLOADS = [
    '"; rm -rf /tmp/x; echo "',
    "back\\slash",
    "colon: semi; comma,",
    "line1\nline2\rline3",
    "{\\c&HFFFFFF&}OWNED{\\c}",
    "{\\move(0,0,100,100)}",
    "\\N\\h",
    "emoji 🎬🔥💯",
    "RTL \u202b\u05e2\u05d1\u05e8\u05d9\u05ea\u202c",
    "Devanagari नRouterस्ते",
    'quote\'s "double" `back`',
    "percent %s %d 100%",
    "tab\there",
    "null\x00byte",
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_escape_neutralizes_control_syntax(payload: str) -> None:
    escaped = escape_ass(payload)
    assert "{" not in escaped.replace("\\{", "")
    assert "}" not in escaped.replace("\\}", "")
    assert "\n" not in escaped and "\r" not in escaped


def test_ass_with_all_payloads_is_wellformed() -> None:
    cues = [
        CaptionCue(id=f"w{i}", start=i * 0.5, end=i * 0.5 + 0.4, text=p)
        for i, p in enumerate(PAYLOADS)
    ]
    doc = ass_for_cues(cues)  # highlight=None: no legit override blocks
    assert doc.count("Dialogue:") == len(PAYLOADS)
    for line in doc.splitlines():
        if line.startswith("Dialogue:"):
            text = line.split(",,")[-1]
            for i, ch in enumerate(text):
                if ch in "{}":
                    assert i > 0 and text[i - 1] == "\\", f"unescaped {ch} in {text!r}"


@pytest.mark.integration
async def test_full_render_with_hostile_captions() -> None:
    """End-to-end: payload captions render to a valid mp4 (slow: runs in integration pass)."""
    words = [
        Word(
            id=f"w{i + 1}",
            start=i * 0.2,
            end=i * 0.2 + 0.18,
            text=p[:20],
            display_text=p,
            lang="en",
        )
        for i, p in enumerate(PAYLOADS[:8])
    ]
    tl = Timeline(
        project_id=uuid.uuid4(),
        transcript=Transcript(words=words),
        video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=2.0)],
        captions=CaptionConfig(),
        output=OutputSpec(),
    )
    store = get_storage(get_settings())

    def _run() -> str:
        with tempfile.TemporaryDirectory(prefix="clipz-fuzz-") as tmp:
            master = str(Path(tmp) / "master.mp4")
            Path(master).write_bytes(Path("fixtures/synth/bars_5s.mp4").read_bytes())
            out = asyncio.run(render_timeline(tl, master, {}, PREVIEW, store, tmp, lambda f: None))
            data = store.get_bytes(str(out["key"]))
            target = f"/tmp/fuzz_{uuid.uuid4().hex}.mp4"
            Path(target).write_bytes(data)
            return target

    path = await asyncio.to_thread(_run)
    try:
        info = probe_file(path)
        assert (info.width, info.height) == (540, 960)
    finally:
        os.unlink(path)
