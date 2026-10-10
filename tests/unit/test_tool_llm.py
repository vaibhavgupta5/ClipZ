"""Tool-calling provider layer: Gemini Interactions mapping (offline) + scripted fake."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.providers.base import ChatMessage, ChatRequest
from app.providers.fakes import ScriptedToolLLM
from app.providers.gemini import (
    GeminiLLM,
    ProviderFailed,
    ProviderUnavailable,
    build_body,
    parse_interaction,
    to_steps,
)
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
                calls=[ToolCall(id="fc_1", name="remove_silence", args={"min_silence_ms": 500})],
            ),
            AgentMessage(
                role="user",
                results=[ToolResult(call_id="fc_1", name="remove_silence", content="{}")],
            ),
        ],
    )


def test_build_body_is_stateless_and_maps_history() -> None:
    body = build_body(_request(), "gemini-3.8-flash", "low")
    assert body["store"] is False and "previous_interaction_id" not in body
    assert body["system_instruction"] == "You edit reels."
    assert body["generation_config"] == {"max_output_tokens": 4096, "thinking_level": "low"}
    assert (
        body["tools"][0]["type"] == "function"
        and body["tools"][0]["parameters"]["type"] == "object"
    )
    assert [s["type"] for s in body["input"]] == [
        "user_input",
        "model_output",
        "function_call",
        "function_result",
    ]
    assert body["input"][2] == {
        "type": "function_call",
        "id": "fc_1",
        "name": "remove_silence",
        "arguments": {"min_silence_ms": 500},
    }
    assert body["input"][3]["call_id"] == "fc_1" and body["input"][3]["is_error"] is False


def test_native_steps_are_replayed_verbatim() -> None:
    native = [
        {"type": "thought", "signature": "c2ln"},
        {"type": "function_call", "id": "a", "name": "denoise", "arguments": {}},
    ]
    steps = to_steps([AgentMessage(role="assistant", text="ignored", native=native)])
    assert steps == native


def _interaction(status: str = "requires_action") -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        model="gemini-3.8-flash",
        steps=[
            SimpleNamespace(
                type="thought", model_dump=lambda **_: {"type": "thought", "signature": "c2ln"}
            ),
            {"type": "model_output", "content": [{"type": "text", "text": "On it."}]},
            {
                "type": "function_call",
                "id": "fc_9",
                "name": "denoise",
                "arguments": {"enabled": True},
            },
        ],
        usage=SimpleNamespace(total_input_tokens=10, total_output_tokens=5, total_cached_tokens=7),
    )


def test_parse_interaction_extracts_text_calls_usage_and_native() -> None:
    reply = parse_interaction(_interaction())
    assert reply.text == "On it." and reply.stop_reason == "tool_use"
    assert reply.calls == [ToolCall(id="fc_9", name="denoise", args={"enabled": True})]
    assert reply.usage.input_tokens == 10 and reply.usage.cache_read_tokens == 7
    assert reply.native[0] == {"type": "thought", "signature": "c2ln"}  # type: ignore[index]
    # completed but with calls still means "run the tools"
    assert parse_interaction(_interaction("completed")).stop_reason == "tool_use"
    assert parse_interaction(_interaction("incomplete")).stop_reason == "max_tokens"
    with pytest.raises(ProviderFailed):
        parse_interaction(_interaction("failed"))


class _FakeInteractions:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.bodies: list[dict[str, Any]] = []

    async def create(self, **body: Any) -> Any:
        self.bodies.append(body)
        return self.result


def _client(result: Any) -> tuple[SimpleNamespace, _FakeInteractions]:
    fake = _FakeInteractions(result)
    return SimpleNamespace(aio=SimpleNamespace(interactions=fake)), fake


async def test_gemini_tool_turn_and_text_sink() -> None:
    client, fake = _client(_interaction("completed"))
    llm = GeminiLLM(api_key="", client=client)
    assert isinstance(llm, ToolCallingLLM)
    deltas: list[str] = []
    reply = await llm.complete_with_tools(_request(), on_text=deltas.append)
    assert deltas == ["On it."] and reply.calls[0].name == "denoise"
    assert fake.bodies[0]["model"] == "gemini-2.5-flash-lite"


async def test_gemini_plain_completion_for_analysis() -> None:
    done = SimpleNamespace(
        status="completed",
        model="gemini-3.8-flash",
        steps=[{"type": "model_output", "content": [{"type": "text", "text": '{"beats": []}'}]}],
        usage=None,
    )
    client, fake = _client(done)
    reply = await GeminiLLM(api_key="", client=client).complete(
        ChatRequest(
            messages=[
                ChatMessage(role="system", content="Return JSON."),
                ChatMessage(role="user", content="transcript"),
            ]
        )
    )
    assert reply.text == '{"beats": []}'
    body = fake.bodies[0]
    assert "tools" not in body and body["system_instruction"] == "Return JSON."
    assert body["input"] == [
        {"type": "user_input", "content": [{"type": "text", "text": "transcript"}]}
    ]


async def test_gemini_without_key_is_unavailable() -> None:
    with pytest.raises(ProviderUnavailable, match="GEMINI_API_KEY"):
        await GeminiLLM(api_key="").complete_with_tools(_request())


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
