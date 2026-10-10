"""Provider-neutral tool-calling contract (one model turn at a time).

The Director owns the loop (iteration caps, validation, versioning); a provider
only turns (system, history, tools) into one assistant turn. History is kept in
a neutral shape so Claude and Gemini share the loop; ``native`` carries the
provider's own assistant content (e.g. thinking blocks) for verbatim replay.
"""

from collections.abc import Callable
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

Frozen = ConfigDict(frozen=True)


class ToolSpec(BaseModel):
    """One tool as the model sees it (exported from app/tools/registry)."""

    model_config = Frozen

    name: str
    description: str
    input_schema: dict[str, object]


class ToolCall(BaseModel):
    """A tool invocation requested by the model. ``args`` are UNVALIDATED."""

    model_config = Frozen

    id: str = Field(description="Provider call ID, echoed back in the ToolResult")
    name: str
    args: dict[str, object] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Executor output for one call, sent back to the model."""

    model_config = Frozen

    call_id: str
    name: str
    content: str = Field(description="JSON or error text for the model")
    is_error: bool = False


class AgentMessage(BaseModel):
    """One neutral history entry."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    role: Literal["user", "assistant"]
    text: str = ""
    calls: list[ToolCall] = Field(default_factory=list)
    results: list[ToolResult] = Field(default_factory=list)
    native: object | None = Field(default=None, exclude=True, description="Provider replay blob")


class Usage(BaseModel):
    """Token accounting for one turn (cache fields 0 when unsupported)."""

    model_config = Frozen

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    thought_tokens: int = 0


StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


class ToolTurnRequest(BaseModel):
    """Everything one turn needs. ``system`` and ``tools`` form the cacheable prefix."""

    model_config = Frozen

    system: str
    messages: list[AgentMessage]
    tools: list[ToolSpec]
    max_tokens: int = Field(default=4096, ge=1)


class ToolTurnReply(BaseModel):
    """One assistant turn."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    text: str = ""
    calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: StopReason = "end_turn"
    model: str = ""
    usage: Usage = Field(default_factory=Usage)
    native: object | None = Field(default=None, exclude=True)

    def as_message(self) -> AgentMessage:
        """History entry for this turn (keeps the native blob for replay)."""
        return AgentMessage(role="assistant", text=self.text, calls=self.calls, native=self.native)


TextSink = Callable[[str], None]


@runtime_checkable
class ToolCallingLLM(Protocol):
    """A model that can plan with tools. Implementations: Claude, Gemini, scripted fake."""

    provider_name: str
    model: str

    async def complete_with_tools(
        self, request: ToolTurnRequest, on_text: TextSink | None = None
    ) -> ToolTurnReply:
        """Run one assistant turn; stream text deltas to ``on_text`` when given."""
        ...
