"""Gemini adapter over the Interactions API (google-genai SDK), stateless (ADR-012).

History is sent as typed input steps with ``store=false`` so Google keeps nothing:
user turns -> ``user_input``, our tool results -> ``function_result``, and the
model's own earlier steps (thoughts with signatures, function calls, outputs)
are replayed verbatim from ``native``. Request building and response parsing
are pure functions, tested offline; the SDK client is created lazily.
Docs: https://ai.google.dev/gemini-api/docs/function-calling (retrieved 2026-10-10).
"""

from typing import Any

from app.providers.base import ChatReply, ChatRequest
from app.providers.tools import (
    AgentMessage,
    StopReason,
    TextSink,
    ToolCall,
    ToolTurnReply,
    ToolTurnRequest,
    Usage,
)

DEFAULT_MODEL = "gemini-2.5-flash-lite"
_STATUS: dict[str, StopReason] = {
    "completed": "end_turn",
    "requires_action": "tool_use",
    "incomplete": "max_tokens",
    "budget_exceeded": "max_tokens",
}


class ProviderUnavailable(RuntimeError):
    """Key unset or SDK missing; the message says how to fix it."""


class ProviderFailed(RuntimeError):
    """The model run failed or was cancelled."""


def _text(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}]


def to_steps(messages: list[AgentMessage]) -> list[dict[str, Any]]:
    """Neutral history -> Interactions input steps."""
    steps: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            if isinstance(m.native, list):
                steps.extend(m.native)
                continue
            if m.text:
                steps.append({"type": "model_output", "content": _text(m.text)})
            steps += [
                {"type": "function_call", "id": c.id, "name": c.name, "arguments": c.args}
                for c in m.calls
            ]
            continue
        steps += [
            {
                "type": "function_result",
                "call_id": r.call_id,
                "name": r.name,
                "result": r.content,
                "is_error": r.is_error,
            }
            for r in m.results
        ]
        if m.text:
            steps.append({"type": "user_input", "content": _text(m.text)})
    return steps


def build_body(request: ToolTurnRequest, model: str, thinking_level: str) -> dict[str, Any]:
    """Interactions create() body for one tool turn."""
    return {
        "model": model,
        "system_instruction": request.system,
        "input": to_steps(request.messages),
        "tools": [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            }
            for t in request.tools
        ],
        "generation_config": {
            "max_output_tokens": request.max_tokens,
            # empty = model default (2.5 Flash-Lite: no thinking; its "low" maps to an
            # invalid 256-token budget, so the field must be omitted for it)
            **({"thinking_level": thinking_level} if thinking_level else {}),
        },
        "store": False,
    }


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _dump(step: Any) -> dict[str, Any]:
    if isinstance(step, dict):
        return step
    dumped: dict[str, Any] = step.model_dump(mode="json", by_alias=True, exclude_none=True)
    return dumped


def parse_interaction(interaction: Any) -> ToolTurnReply:
    """SDK Interaction -> neutral reply. Function-call arguments stay unvalidated."""
    status = str(_get(interaction, "status") or "")
    if status in ("failed", "cancelled"):
        raise ProviderFailed(f"gemini interaction {status}: {_get(interaction, 'errors')}")
    steps = list(_get(interaction, "steps") or [])
    texts: list[str] = []
    calls: list[ToolCall] = []
    for step in steps:
        kind = _get(step, "type")
        if kind == "function_call":
            args = _get(step, "arguments")
            calls.append(
                ToolCall(
                    id=str(_get(step, "id")),
                    name=str(_get(step, "name")),
                    args=args if isinstance(args, dict) else {},
                )
            )
        elif kind == "model_output":
            texts += [
                str(_get(c, "text"))
                for c in _get(step, "content") or []
                if _get(c, "type") == "text"
            ]
    stop = _STATUS.get(status, "other")
    if calls and stop == "end_turn":
        stop = "tool_use"
    u = _get(interaction, "usage")
    usage = Usage(
        input_tokens=int(_get(u, "total_input_tokens") or 0) if u else 0,
        output_tokens=int(_get(u, "total_output_tokens") or 0) if u else 0,
        cache_read_tokens=int(_get(u, "total_cached_tokens") or 0) if u else 0,
        thought_tokens=int(_get(u, "total_thought_tokens") or 0) if u else 0,
    )
    return ToolTurnReply(
        text="".join(texts),
        calls=calls,
        stop_reason=stop,
        model=str(_get(interaction, "model") or ""),
        usage=usage,
        native=[_dump(s) for s in steps],
    )


class GeminiLLM:
    """Gemini as both LLMProvider (analysis stages) and ToolCallingLLM (Director)."""

    provider_name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        thinking_level: str = "",
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.thinking_level = thinking_level
        self._api_key = api_key
        self._client = client

    def _interactions(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise ProviderUnavailable("GEMINI_API_KEY is empty; set it to use Gemini")
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client.aio.interactions

    async def complete_with_tools(
        self, request: ToolTurnRequest, on_text: TextSink | None = None
    ) -> ToolTurnReply:
        """One tool turn (non-streamed; the full reply text is emitted once)."""
        body = build_body(request, self.model, self.thinking_level)
        reply = parse_interaction(await self._interactions().create(**body))
        if on_text is not None and reply.text:
            on_text(reply.text)
        return reply

    async def complete(self, request: ChatRequest) -> ChatReply:
        """Plain completion for analysis stages (beats, romanization, emphasis)."""
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        history = [
            AgentMessage(role="user" if m.role == "user" else "assistant", text=m.content)
            for m in request.messages
            if m.role != "system"
        ]
        body = build_body(
            ToolTurnRequest(
                system=system, messages=history, tools=[], max_tokens=request.max_tokens
            ),
            self.model,
            self.thinking_level,
        )
        del body["tools"]
        body["generation_config"]["temperature"] = request.temperature
        reply = parse_interaction(await self._interactions().create(**body))
        return ChatReply(
            text=reply.text,
            model=reply.model or self.model,
            tokens_used=reply.usage.input_tokens + reply.usage.output_tokens,
        )
