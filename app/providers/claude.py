"""Claude tool-calling adapter (Messages API, streamed, prompt-cached).

The SDK is imported lazily so unit tests and `make check` never need it.
Request building and response parsing are pure functions, tested offline.
Docs: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview (ADR-008).
"""

from typing import Any

from app.providers.tools import (
    AgentMessage,
    StopReason,
    TextSink,
    ToolCall,
    ToolTurnReply,
    ToolTurnRequest,
    Usage,
)

DEFAULT_MODEL = "claude-opus-5"
# Models that take adaptive thinking + output_config.effort.
_ADAPTIVE = {
    "claude-opus-5",
    "claude-opus-5-5",
    "claude-sonnet-5",
    "claude-fable-5-1",
    "claude-opus-4-8",
}
# Models with server-side refusal fallback (beta).
_FALLBACK = {"claude-opus-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_CACHE = {"type": "ephemeral"}
_STOPS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


class ProviderUnavailable(RuntimeError):
    """SDK missing or key unset; message says how to fix it."""


def build_params(request: ToolTurnRequest, model: str, effort: str) -> dict[str, Any]:
    """Messages API kwargs. Tools then system form the cached prefix (stable order)."""
    tools: list[dict[str, Any]] = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
            "eager_input_streaming": True,  # inputs are re-validated by the executor
        }
        for t in request.tools
    ]
    if tools:
        tools[-1]["cache_control"] = _CACHE
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": request.max_tokens,
        "system": [{"type": "text", "text": request.system, "cache_control": _CACHE}],
        "tools": tools,
        "messages": [to_claude_message(m) for m in request.messages],
    }
    if model in _ADAPTIVE:
        params["thinking"] = {"type": "adaptive"}
        params["output_config"] = {"effort": effort}
    if model in _FALLBACK:
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


def to_claude_message(m: AgentMessage) -> dict[str, Any]:
    """Neutral history entry -> Claude message. Assistant turns replay native content."""
    if m.role == "assistant":
        if m.native is not None:
            return {"role": "assistant", "content": m.native}
        content: list[dict[str, Any]] = []
        if m.text:
            content.append({"type": "text", "text": m.text})
        content += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in m.calls
        ]
        return {"role": "assistant", "content": content}
    blocks: list[dict[str, Any]] = [
        {
            "type": "tool_result",
            "tool_use_id": r.call_id,
            "content": r.content,
            "is_error": r.is_error,
        }
        for r in m.results
    ]
    if m.text:
        blocks.append({"type": "text", "text": m.text})
    return {"role": "user", "content": blocks}


def parse_message(msg: Any) -> ToolTurnReply:
    """SDK Message -> neutral reply. Tool inputs stay unvalidated (executor checks them)."""
    text = "".join(b.text for b in msg.content if b.type == "text")
    calls = [
        ToolCall(id=b.id, name=b.name, args=b.input if isinstance(b.input, dict) else {})
        for b in msg.content
        if b.type == "tool_use"
    ]
    u = msg.usage
    usage = Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
    return ToolTurnReply(
        text=text,
        calls=calls,
        stop_reason=_STOPS.get(msg.stop_reason or "", "other"),
        model=msg.model,
        usage=usage,
        native=list(msg.content),
    )


class ClaudeToolLLM:
    """ToolCallingLLM over the Anthropic SDK (async, streamed)."""

    provider_name = "claude"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        effort: str = "medium",
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self._api_key = api_key
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise ProviderUnavailable("ANTHROPIC_API_KEY is empty; set it to use Claude")
            try:
                import anthropic  # type: ignore[import-not-found,unused-ignore]
            except ImportError as exc:
                raise ProviderUnavailable("install the SDK: uv add anthropic") from exc
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def complete_with_tools(
        self, request: ToolTurnRequest, on_text: TextSink | None = None
    ) -> ToolTurnReply:
        """One streamed assistant turn."""
        client = self._get_client()
        params = build_params(request, self.model, self.effort)
        async with client.beta.messages.stream(**params) as stream:
            async for delta in stream.text_stream:
                if on_text is not None:
                    on_text(delta)
            message = await stream.get_final_message()
        return parse_message(message)
