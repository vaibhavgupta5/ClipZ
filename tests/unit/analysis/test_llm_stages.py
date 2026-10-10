"""LLM stage tests: strict JSON beats, 1:1 romanization, prosody x tags emphasis."""

import json

import pytest

from app.analysis.beats import detect_beats
from app.analysis.emphasis import tag_emphasis
from app.analysis.models import EnergyCurve
from app.analysis.romanize import romanize
from app.providers.base import ChatReply, ChatRequest
from app.timeline.transcript import Transcript, Word


def _transcript() -> Transcript:
    return Transcript(
        words=[
            Word(id="w1", start=0.0, end=0.4, text="hello", display_text="hello", lang="en"),
            Word(id="w2", start=0.5, end=0.9, text="world", display_text="world", lang="en"),
        ],
        language="en",
    )


class ScriptedLLM:
    """LLMProvider returning canned replies in order."""

    provider_name = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls = 0

    async def complete(self, request: ChatRequest) -> ChatReply:
        _ = request
        self.calls += 1
        return ChatReply(text=self._replies.pop(0), model="scripted", tokens_used=10)


def _beats_doc() -> str:
    return json.dumps(
        {
            "beats": [
                {
                    "beat_id": "b1",
                    "words": ["w1", "w2"],
                    "intent": "illustrate",
                    "query_hint": "globe",
                    "tone": "excited",
                }
            ]
        }
    )


async def test_beats_valid_json() -> None:
    beats = await detect_beats(_transcript(), ScriptedLLM([_beats_doc()]))
    assert len(beats) == 1 and beats[0].id == "b1" and beats[0].intent == "illustrate"


async def test_beats_retry_after_invalid_then_valid() -> None:
    llm = ScriptedLLM(['{"beats": "oops"}', _beats_doc()])
    beats = await detect_beats(_transcript(), llm)
    assert len(beats) == 1 and llm.calls == 2


async def test_beats_reject_unknown_word_ids() -> None:
    doc = json.dumps({"beats": [{"beat_id": "b1", "words": ["w99"], "intent": "joke"}]})
    with pytest.raises(ValueError, match="w99"):
        await detect_beats(_transcript(), ScriptedLLM([doc, doc]))


async def test_romanize_keeps_timings_and_count() -> None:
    doc = json.dumps({"words": ["hello", "duniya"]})
    out = await romanize(_transcript(), ScriptedLLM([doc]))
    assert [w.display_text for w in out.words] == ["hello", "duniya"]
    assert [(w.start, w.end) for w in out.words] == [(0.0, 0.4), (0.5, 0.9)]
    assert [w.id for w in out.words] == ["w1", "w2"]


async def test_romanize_rejects_count_mismatch() -> None:
    doc = json.dumps({"words": ["only-one"]})
    with pytest.raises(ValueError, match="word count"):
        await romanize(_transcript(), ScriptedLLM([doc, doc]))


async def test_emphasis_combines_prosody_and_tags() -> None:
    energy = EnergyCurve(bucket_ms=100, values=[0.1] * 5 + [0.9] * 5)  # w2 louder
    tags = json.dumps({"emphasis": ["w1"]})  # LLM votes w1
    marks = await tag_emphasis(_transcript(), energy, ScriptedLLM([tags]), top_n=1)
    assert len(marks) == 1
    assert marks[0].word_id == "w2"  # 0.6 prosody beats 0.4 tag here
