"""Unit tests for the Gemini Video Critic and director self-correction loop."""

import json

import pytest

from app.agent.context import system_prompt
from app.agent.critic import (
    CriticReport,
    _parse_json_report,
    evaluate_preview,
    evaluate_timeline_heuristic,
    sample_frame_timestamps,
)
from app.agent.director import Event, run_director
from app.providers.base import ChatReply, ChatRequest, LLMProvider
from app.providers.fakes import ScriptedToolLLM
from app.providers.tools import ToolCall, ToolTurnReply, ToolTurnRequest
from app.timeline import ops
from app.timeline.schema import AudioEffect, CaptionConfig, Zoom
from tests.unit.agent.memory_ws import MemoryWorkspace
from tests.unit.timeline.test_ops import _timeline


def test_sample_frame_timestamps() -> None:
    # 0s or negative
    assert sample_frame_timestamps(0.0) == []
    assert sample_frame_timestamps(-5.0) == []

    # very short video
    assert sample_frame_timestamps(0.8) == [0.4]

    # 3s video
    ts3 = sample_frame_timestamps(3.0, count=3)
    assert len(ts3) == 3
    assert all(0 <= t <= 3.0 for t in ts3)

    # 15s reel
    ts15 = sample_frame_timestamps(15.0, count=5)
    assert len(ts15) == 5
    assert ts15[0] == 0.5
    assert ts15[1] == 1.8
    assert ts15[-1] <= 15.0


def test_evaluate_timeline_heuristic_unpolished() -> None:
    tl = _timeline().model_copy(update={"captions": CaptionConfig(mode="off")})
    report = evaluate_timeline_heuristic(tl)
    assert isinstance(report, CriticReport)
    assert report.verdict == "needs_edits"
    assert any("Captions are disabled" in iss for iss in report.issues)
    assert len(report.criteria) == 5
    assert len(report.suggested_edits) > 0


def test_evaluate_timeline_heuristic_polished() -> None:
    tl = _timeline().model_copy(
        update={
            "captions": CaptionConfig(mode="word_highlight", size_px=96),
            "transition": "zoom_in",
        }
    )
    tl, _ = ops.add_zoom(tl, Zoom(id="z1", anchor_word="w1", scale=1.15, dur_ms=400))
    tl, _ = ops.set_audio_fx(tl, [AudioEffect(type="denoise")])
    report = evaluate_timeline_heuristic(tl)
    assert isinstance(report, CriticReport)
    assert report.overall_score >= 8
    assert report.verdict == "pass"


def test_parse_json_report() -> None:
    valid = json.dumps(
        {
            "overall_score": 9,
            "verdict": "pass",
            "criteria": [
                {"name": "hook_strength", "score": 9, "comment": "Great hook"},
                {"name": "pacing", "score": 8, "comment": "Fast"},
                {"name": "caption_legibility", "score": 9, "comment": "Clear"},
                {"name": "visual_variety", "score": 8, "comment": "Dynamic"},
                {"name": "audio_balance", "score": 9, "comment": "Clean audio"},
            ],
            "issues": [],
            "suggested_edits": [],
            "summary": "Looks great!",
        }
    )
    wrapped = f"Here is my review:\n```json\n{valid}\n```"
    report = _parse_json_report(wrapped)
    assert report is not None
    assert report.overall_score == 9
    assert report.verdict == "pass"

    assert _parse_json_report("not json at all") is None
    assert _parse_json_report("{broken json") is None


class ScriptedLLMProvider(LLMProvider):
    provider_name = "scripted_critic"

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text

    async def complete(self, request: ChatRequest) -> ChatReply:
        return ChatReply(text=self.response_text, model="test-critic", tokens_used=100)


@pytest.mark.asyncio
async def test_evaluate_preview_with_llm() -> None:
    valid_json = json.dumps(
        {
            "overall_score": 7,
            "verdict": "needs_edits",
            "criteria": [
                {"name": "hook_strength", "score": 6, "comment": "Add opening zoom"},
            ],
            "issues": ["Weak hook"],
            "suggested_edits": ["add_zoom anchor_word='w1'"],
            "summary": "Needs stronger opening",
        }
    )
    llm = ScriptedLLMProvider(valid_json)
    tl = _timeline()
    report = await evaluate_preview(llm, tl, user_prompt="make it punchy")
    assert report.overall_score == 7
    assert report.verdict == "needs_edits"
    assert report.issues == ["Weak hook"]


@pytest.mark.asyncio
async def test_director_critic_self_correction_loop() -> None:
    # First turn: model adds captions.
    # Critic responds with needs_edits.
    # Second turn: model sets transition based on critic feedback.
    # Critic passes.
    ws = MemoryWorkspace(_timeline())
    events: list[Event] = []

    def call1(req: ToolTurnRequest) -> ToolTurnReply:
        return ToolTurnReply(
            calls=[ToolCall(id="c1", name="add_captions", args={"size_px": 96})],
            stop_reason="tool_use",
        )

    def done1(req: ToolTurnRequest) -> ToolTurnReply:
        return ToolTurnReply(text="Added captions.")

    def fix_turn(req: ToolTurnRequest) -> ToolTurnReply:
        # Verify the critic message was injected into user turns
        user_texts = [m.text for m in req.messages if m.role == "user" and m.text]
        assert any("Video Critic Review" in t for t in user_texts)
        return ToolTurnReply(
            calls=[ToolCall(id="c2", name="set_transition", args={"style": "zoom_in"})],
            stop_reason="tool_use",
        )

    def done2(req: ToolTurnRequest) -> ToolTurnReply:
        return ToolTurnReply(text="Applied critic suggestions.")

    from collections.abc import Callable

    script: list[ToolTurnReply | Callable[[ToolTurnRequest], ToolTurnReply]] = [
        call1,
        done1,
        fix_turn,
        done2,
    ]
    llm = ScriptedToolLLM(script)

    res = await run_director(
        llm=llm,
        ws=ws,
        system=system_prompt("data"),
        message="edit the video",
        emit=events.append,
        enable_critic=True,
        max_critic_iters=2,
    )

    assert len(res.versions) >= 2
    assert len(res.critic_reports) >= 1
    critic_events = [e for e in events if e.type == "critic_feedback"]
    assert len(critic_events) >= 1
