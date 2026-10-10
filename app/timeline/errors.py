"""Structured errors written for the LLM to self-correct from (rules 3, 6)."""

from pydantic import BaseModel, ConfigDict, Field


class ToolError(BaseModel):
    """One actionable validation failure."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(description="Machine-readable code, e.g. 'unknown_word'")
    field: str = Field(description="Offending field or anchor, e.g. 'anchor_word=w99'")
    message: str = Field(description="What went wrong, with the offending value")
    hint: str = Field(description="How to fix it, e.g. valid range or available IDs")


class TimelineInvalid(ValueError):
    """Raised by ensure_valid(); carries all ToolErrors."""

    def __init__(self, errors: list[ToolError]) -> None:
        self.errors = errors
        summary = "; ".join(f"{e.code}({e.field}): {e.message}" for e in errors)
        super().__init__(f"invalid timeline: {summary}")


class OpError(ValueError):
    """Raised when an op cannot apply; message is written for the LLM."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        self.code = code
        self.hint = hint
        super().__init__(f"[{code}] {message}" + (f" Hint: {hint}" if hint else ""))
