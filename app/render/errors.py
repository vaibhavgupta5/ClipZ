"""Render errors: compilation failures with LLM/actionable codes."""


class RenderError(RuntimeError):
    """EDL compilation or render failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")
