"""Studio page + brainstorm parsing (no server needed)."""

from app.api.studio import BRAINSTORM, FINAL_RE, PAGE


def test_page_wires_every_feature() -> None:
    html = PAGE.read_text(encoding="utf-8")
    for needle in (
        "/brainstorm",  # brainstorm mode
        "/messages",  # Director chat (SSE)
        "/uploads",  # upload
        "/renders",  # results list
        "download",  # download button
        "X-API-Key",  # media fetched with the key, never in a URL
        "plan_edit",  # plan card
        "/source",  # original video tab
        'data-tab="original"',
        "resetPanels(p)",  # switching project clears the right panel
    ):
        assert needle in html, needle
    assert 'innerHTML = `<video src="${src}"' in html  # blob URL, not a raw media URL


def test_final_prompt_is_split_from_the_reply() -> None:
    text = "Ideas:\n- logo on w3\n\nFINAL PROMPT: remove pauses, add captions"
    match = FINAL_RE.search(text)
    assert match and match.group(1) == "remove pauses, add captions"
    assert BRAINSTORM.startswith("ROLE") and "FINAL PROMPT:" in BRAINSTORM
