"""ASS caption tests: escaping, per-word timing, safe areas, Devanagari."""

from app.render.captions_ass import ass_for_words, escape_ass
from app.timeline.transcript import Word


def _words() -> list[Word]:
    return [
        Word(id="w1", start=1.2, end=1.55, text="BILLIONS", display_text="BILLIONS", lang="en"),
        Word(
            id="w2",
            start=1.6,
            end=1.9,
            text=" Proofs: {x}\\y\nline",
            display_text="Proofs",
            lang="en",
        ),
    ]


def test_escape_ass() -> None:
    assert escape_ass("a{b}\\c") == "a\\{b\\}\\\\c"
    assert escape_ass("l1\nl2") == "l1\\Nl2"
    assert escape_ass("a:b;c") == "a:b;c"  # colons/semicolons are fine in ASS
    assert escape_ass("नमस्ते 🙏") == "नमस्ते 🙏"  # shaping + emoji pass through


def test_ass_dialogue_per_word_with_highlight() -> None:
    doc = ass_for_words(_words(), highlight="w1")
    assert "Dialogue: 0,0:00:01.20,0:00:01.55,Pop,,0,0,0,,{\\c&H0000D4FF&}BILLIONS{\\r}" in doc
    assert "Dialogue: 0,0:00:01.60,0:00:01.90,Pop,,0,0,0,,Proofs" in doc


def test_ass_safe_areas_and_fonts() -> None:
    doc = ass_for_words(_words())
    assert "PlayResX: 1080" in doc and "PlayResY: 1920" in doc
    assert ",60,60,340,1" in doc  # MarginL,MarginR,MarginV: bottom strip clear
    assert "Liberation Sans" in doc
    doc_hi = ass_for_words(_words(), font="devanagari")
    assert "Noto Sans Devanagari" in doc_hi


def test_ass_header_styles_present() -> None:
    doc = ass_for_words(_words())
    assert "[V4+ Styles]" in doc and "Format:" in doc and "[Events]" in doc


def test_ass_color_converts_and_rejects() -> None:
    import pytest

    from app.render.captions_ass import ass_color

    assert ass_color("#FFD400") == "&H0000D4FF&"
    with pytest.raises(ValueError):
        ass_color("&H00FFFFFF&,evil")


def test_word_highlight_emits_one_event_per_word_with_style() -> None:
    from app.render.captions_ass import CaptionCue, ass_for_cues

    cues = [
        CaptionCue(id="w1", start=0.0, end=0.4, text="hi"),
        CaptionCue(id="w2", start=0.5, end=0.9, text="there"),
    ]
    doc = ass_for_cues(
        cues, words_per_line=2, mode="word_highlight", size_px=120, highlight_color="#00FF00"
    )
    events = [ln for ln in doc.splitlines() if ln.startswith("Dialogue:")]
    assert len(events) == 2
    assert "{\\c&H0000FF00&}hi{\\r} there" in events[0]
    assert "hi {\\c&H0000FF00&}there{\\r}" in events[1]
    assert ",120,&H00FFFFFF," in doc


def test_uppercase_pop_animation_on_active_word() -> None:
    from app.render.captions_ass import CaptionCue, ass_for_cues

    cues = [
        CaptionCue(id="w1", start=0.0, end=0.4, text="ship"),
        CaptionCue(id="w2", start=0.5, end=0.9, text="it"),
    ]
    doc = ass_for_cues(
        cues, words_per_line=2, mode="word_highlight", uppercase=True, animation="pop"
    )
    first = next(ln for ln in doc.splitlines() if ln.startswith("Dialogue:"))
    assert "\\t(0,90,\\fscx112\\fscy112)" in first and "SHIP{\\r} IT" in first
