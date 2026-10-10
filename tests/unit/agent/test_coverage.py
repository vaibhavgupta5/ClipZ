"""Request coverage: phrases -> tools, negation, transition subtlety."""

from app.agent.coverage import missing, requested
from tests.unit.timeline.test_ops import _timeline

PROMPT = (
    "Make it vertical, cut the dead air, clean the noise, add sad music, captions, "
    "a few images and one meme, smooth transitions, punch-in zooms and subtle sound "
    "effects. Export."
)


def test_requested_items_are_recognised() -> None:
    labels = {r.label for r in requested(PROMPT)}
    assert labels == {
        "images / memes / GIFs", "background music", "captions", "punch-in zooms",
        "cut transitions", "sound effects", "noise reduction", "silence removal",
        "vertical 9:16 framing", "export",
    }  # fmt: skip


def test_negated_mentions_are_not_requests() -> None:
    labels = {r.label for r in requested("add captions but no music and without memes")}
    assert labels == {"captions"}


def test_missing_reports_skipped_items_and_none_transition() -> None:
    applied = {"reframe_9x16", "remove_silence", "denoise", "add_music", "set_caption_style",
               "set_transition", "add_zooms", "auto_sfx", "export"}  # fmt: skip
    tl = _timeline()  # transition defaults to "none"
    assert missing(PROMPT, applied, tl) == ["images / memes / GIFs", "cut transitions"]
    done = tl.model_copy(update={"transition": "flash"})
    assert missing(PROMPT, applied | {"add_inserts"}, done) == []
