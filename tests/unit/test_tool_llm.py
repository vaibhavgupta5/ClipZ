"""Tool-calling provider layer: Claude request/response mapping (offline) + scripted fake."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.providers.claude import ClaudeToolLLM, ProviderUnavailable, build_params, parse_message
from app.providers.fakes import ScriptedToolLLM
from app.providers.tools import (
    AgentMessage,
    ToolCall,
    ToolCallingLLM,
    ToolResult,
    ToolTurnReply,
    ToolTurnRequest,
)
from app.tools.registry import tool_specs


def _request() -> ToolTurnRequest:
    return ToolTurnRequest(
        system="You edit reels.",
        tools=tool_specs(),
        messages=[
            AgentMessage(role="user", text="remove silences"),
            AgentMessage(
                role="assistant",
                text="Cutting.",
                calls=[ToolCall(id="tu_1", name="remove_silence", args={"min_silence_ms": 500})],
            ),
            AgentMessage(
                role="user",
                results=[ToolResult(call_id="tu_1", name="remove_silence", content="{}")],
            ),
        ],
    )


def test_build_params_caches_prefix_and_maps_history() -> None:
    params = build_params(_request(), "claude-opus-5", "medium")
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" in params["tools"][-1]
    assert all("cache_control" not in t for t in params["tools"][:-1])
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "medium"}
    assert params["fallbacks"] == "default"
    assistant = params["messages"][1]["content"]
    assert assistant[1] == {
        "type": "tool_use",
        "id": "tu_1",
        "name": "remove_silence",
        "input": {"min_silence_ms": 500},
    }
    result = params["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1"


def test_build_params_omits_unsupported_features_for_haiku() -> None:
    params = build_params(_request(), "claude-haiku-4-5", "medium")
    assert "thinking" not in params and "fallbacks" not in params and "betas" not in params


def test_native_assistant_content_is_replayed_verbatim() -> None:
    blob = [{"type": "thinking", "thinking": "", "signature": "s"}]
    req = ToolTurnRequest(
        system="s", tools=[], messages=[AgentMessage(role="assistant", native=blob)]
    )
    assert build_params(req, "claude-opus-5", "low")["messages"][0]["content"] is blob


def _sdk_message(stop: str = "tool_use") -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="On it."),
            SimpleNamespace(type="tool_use", id="tu_9", name="denoise", input={"enabled": True}),
        ],
        stop_reason=stop,
        model="claude-opus-5",
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=900,
            cache_creation_input_tokens=None,
        ),
    )


def test_parse_message_extracts_text_calls_usage() -> None:
    reply = parse_message(_sdk_message())
    assert reply.text == "On it." and reply.stop_reason == "tool_use"
    assert reply.calls == [ToolCall(id="tu_9", name="denoise", args={"enabled": True})]
    assert reply.usage.cache_read_tokens == 900 and reply.usage.cache_write_tokens == 0
    assert reply.as_message().native is not None
    assert parse_message(_sdk_message("pause_turn")).stop_reason == "other"


class _FakeStream:
    """Mimics the SDK's async MessageStream: text_stream deltas then the final message."""

    def __init__(self, msg: SimpleNamespace, deltas: list[str]) -> None:
        self.msg = msg
        self.deltas = deltas

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    @property
    def text_stream(self) -> AsyncIterator[str]:
        async def gen() -> AsyncIterator[str]:
            for d in self.deltas:
                yield d

        return gen()

    async def get_final_message(self) -> SimpleNamespace:
        return self.msg


async def test_claude_adapter_streams_text_and_returns_reply() -> None:
    seen: dict[str, Any] = {}

    def stream(**params: Any) -> _FakeStream:
        seen.update(params)
        return _FakeStream(_sdk_message("end_turn"), ["On ", "it."])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    llm = ClaudeToolLLM(api_key="", client=client)
    assert isinstance(llm, ToolCallingLLM)
    deltas: list[str] = []
    reply = await llm.complete_with_tools(_request(), on_text=deltas.append)
    assert deltas == ["On ", "it."] and reply.stop_reason == "end_turn"
    assert seen["model"] == "claude-opus-5"


async def test_claude_adapter_without_key_is_unavailable() -> None:
    with pytest.raises(ProviderUnavailable, match="ANTHROPIC_API_KEY"):
        await ClaudeToolLLM(api_key="").complete_with_tools(_request())


async def test_scripted_llm_replays_and_records() -> None:
    def react(req: ToolTurnRequest) -> ToolTurnReply:
        last = req.messages[-1].results[0]
        return ToolTurnReply(text=f"done: {last.content}")

    llm = ScriptedToolLLM(
        [ToolTurnReply(calls=[ToolCall(id="c1", name="denoise")], stop_reason="tool_use"), react]
    )
    assert isinstance(llm, ToolCallingLLM)
    first = await llm.complete_with_tools(_request())
    assert first.calls[0].name == "denoise" and first.model == "scripted"
    second = await llm.complete_with_tools(_request())
    assert second.text == "done: {}"
    third = await llm.complete_with_tools(_request())
    assert third.calls == [] and len(llm.requests) == 3
