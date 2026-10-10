"""Router: exact one-word commands skip the LLM; everything else goes to the Director.

Matching is deliberately literal (whole message, case/punctuation-insensitive) so
it can never fire on a sentence that merely contains the word. Routing reads only
the user's own chat message, never media-derived text.
"""

import re

from app.providers.tools import ToolCall

_DIRECT: dict[str, tuple[str, dict[str, object]]] = {
    "undo": ("undo", {}),
    "redo": ("redo", {}),
    "export": ("export", {"profile": "final"}),
    "preview": ("preview", {}),
}


def route(message: str) -> ToolCall | None:
    """A direct tool call for a bare command, else None (use the Director)."""
    key = re.sub(r"[^a-z]", "", message.strip().lower())
    hit = _DIRECT.get(key)
    if hit is None or len(message.strip()) > 12:
        return None
    name, args = hit
    return ToolCall(id=f"direct_{name}", name=name, args=dict(args))
