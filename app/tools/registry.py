"""Tool registry: single definition -> Claude/Gemini schemas + dispatcher."""

import difflib

from app.providers.tools import ToolSpec
from app.tools.mvp import TOOLS, ToolDef


class ToolNotFound(KeyError):
    """Unknown tool; message suggests close names."""

    def __init__(self, name: str) -> None:
        known = [t.name for t in TOOLS]
        hints = difflib.get_close_matches(name, known, n=3)
        detail = f"; did you mean: {', '.join(hints)}?" if hints else ""
        super().__init__(f"unknown tool {name!r}{detail}")


def get_tool(name: str) -> ToolDef:
    """Fetch a tool by exact name."""
    for tool in TOOLS:
        if tool.name == name:
            return tool
    raise ToolNotFound(name)


def to_claude_schema() -> dict[str, dict[str, object]]:
    """Claude tool-use schema per tool."""
    return {
        t.name: {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_model.model_json_schema(),
        }
        for t in TOOLS
    }


def to_gemini_schema() -> dict[str, dict[str, object]]:
    """Gemini function-calling schema per tool."""
    return {
        t.name: {
            "name": t.name,
            "description": t.description,
            "parameters": t.input_model.model_json_schema(),
        }
        for t in TOOLS
    }


def tool_specs() -> list[ToolSpec]:
    """Provider-neutral tool list in registry order (stable order keeps the prompt cache warm)."""
    return [
        ToolSpec(
            name=t.name, description=t.description, input_schema=t.input_model.model_json_schema()
        )
        for t in TOOLS
    ]
