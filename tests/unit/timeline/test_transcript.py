"""Transcript model tests: words with stable IDs, beats referencing word ranges."""

import pytest
from pydantic import ValidationError

from app.timeline.transcript import Beat, Transcript, Word


def _words() -> list[Word]:
    return [
        Word(id="w1", start=0.0, end=0.4, text="hello", display_text="hello", lang="en"),
        Word(id="w2", start=0.5, end=0.9, text="world", display_text="world", lang="en"),
    ]


def test_valid_transcript_builds() -> None:
    t = Transcript(
        words=_words(),
        beats=[Beat(id="b1", word_ids=["w1", "w2"], intent="illustrate", query_hint="globe")],
        language="en",
    )
    assert t.word_index["w2"].text == "world"


def test_word_end_before_start_rejected() -> None:
    with pytest.raises(ValidationError, match="end"):
        Word(id="w1", start=0.9, end=0.4, text="x", display_text="x", lang="en")


def test_bad_word_id_format_rejected() -> None:
    with pytest.raises(ValidationError, match="match pattern"):
        Word(id="nope", start=0.0, end=0.4, text="x", display_text="x", lang="en")


def test_beat_with_unknown_word_rejected() -> None:
    with pytest.raises(ValidationError, match="w9"):
        Transcript(words=_words(), beats=[Beat(id="b1", word_ids=["w9"], intent="joke")])
