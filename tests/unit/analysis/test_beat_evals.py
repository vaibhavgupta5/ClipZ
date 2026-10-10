"""Beat eval set: 20 synthetic transcripts, scripted LLM, schema-valid beats (fake-LLM CI eval)."""

import json

import pytest

from app.analysis.beats import detect_beats
from app.providers.base import ChatReply, ChatRequest
from app.timeline.transcript import Transcript, Word

TOPICS = [
    ("money", "illustrate"),
    ("fail", "react"),
    ("punchline", "joke"),
    ("stat", "emphasize"),
    ("proof", "proof"),
    ("shift", "transition"),
    ("cash", "illustrate"),
    ("shock", "react"),
    ("gag", "joke"),
    ("number", "emphasize"),
    ("claim", "proof"),
    ("turn", "transition"),
    ("rich", "illustrate"),
    ("wow", "react"),
    ("lol", "joke"),
    ("data", "emphasize"),
    ("fact", "proof"),
    ("next", "transition"),
    ("gold", "illustrate"),
    ("omg", "react"),
]


class TemplatedLLM:
    """Returns a beat per transcript echoing the requested intent."""

    provider_name = "templated"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: ChatRequest) -> ChatReply:
        self.calls += 1
        user_text = request.messages[-1].content
        intent = "illustrate"
        for _, cand in TOPICS:
            if cand in user_text:
                intent = cand
                break
        return ChatReply(
            text=json.dumps(
                {
                    "beats": [
                        {
                            "beat_id": "b1",
                            "words": ["w1", "w2"],
                            "intent": intent,
                            "query_hint": "thing",
                            "tone": "flat",
                        }
                    ]
                }
            ),
            model="templated",
            tokens_used=5,
        )


def _transcript(topic: str) -> Transcript:
    return Transcript(
        words=[
            Word(id="w1", start=0.0, end=0.4, text=topic, display_text=topic, lang="en"),
            Word(id="w2", start=0.5, end=0.9, text="talk", display_text="talk", lang="en"),
        ]
    )


@pytest.mark.parametrize("topic,intent", TOPICS)
async def test_beat_eval_case(topic: str, intent: str) -> None:
    beats = await detect_beats(_transcript(topic), TemplatedLLM())
    assert len(beats) == 1
    assert beats[0].word_ids == ["w1", "w2"]
    assert beats[0].id == "b1"
