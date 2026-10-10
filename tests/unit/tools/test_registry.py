"""Tool registry tests: single source -> Claude/Gemini schemas; validation errors."""

import pytest
from pydantic import ValidationError

from app.tools.mvp import TOOLS
from app.tools.registry import ToolNotFound, get_tool, to_claude_schema, to_gemini_schema


def test_all_mvp_tools_registered() -> None:
    names = {t.name for t in TOOLS}
    for expected in (
        "get_timeline",
        "undo",
        "redo",
        "trim",
        "delete_range",
        "cut_by_transcript",
        "remove_silence",
        "remove_fillers",
        "add_captions",
        "set_caption_style",
        "denoise",
        "normalize_loudness",
        "reframe_9x16",
        "preview",
        "export",
        "ask_user",
    ):
        assert expected in names, f"missing tool {expected}"


def test_claude_and_gemini_schemas() -> None:
    claude = to_claude_schema()
    gemini = to_gemini_schema()
    assert len(claude) == len(gemini) == len(TOOLS)
    for tool in TOOLS:
        assert claude[tool.name]["input_schema"]["type"] == "object"
        assert gemini[tool.name]["parameters"]["type"] == "object"
        assert tool.description


def test_unknown_tool_names_alternatives() -> None:
    with pytest.raises(ToolNotFound, match="trim"):
        get_tool("trimm")


def test_bad_args_raise_actionable_validation_error() -> None:
    tool = get_tool("trim")
    with pytest.raises(ValidationError) as exc:
        tool.input_model(start_word="w1")
    assert "end_word" in str(exc.value)
