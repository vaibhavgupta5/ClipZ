"""Tool registry: single definition -> Gemini function schemas + neutral ToolSpecs."""

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


ELEVENLABS_TOOLS = frozenset({"generate_sfx", "add_voiceover", "remove_voiceover", "dub_video"})


def tool_specs(exclude: frozenset[str] = frozenset()) -> list[ToolSpec]:
    """Provider-neutral tool list in registry order (stable order keeps the prompt cache warm).

    ``exclude`` hides tools whose backing service is not configured (e.g. ElevenLabs).
    """
    return [
        ToolSpec(
            name=t.name, description=t.description, input_schema=t.input_model.model_json_schema()
        )
        for t in TOOLS
        if t.name not in exclude
    ]
