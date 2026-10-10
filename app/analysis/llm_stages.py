"""LLM text-only stages: beats, romanization, emphasis tags.

Media-derived text is UNTRUSTED DATA (rule 6): it is framed as quoted data in
prompts, outputs are strict-JSON-validated, and timings/IDs always come from
our own transcript — never from the model.
"""

import json
from typing import Any

from app.providers.base import ChatMessage, ChatRequest, LLMProvider
from app.timeline.transcript import Transcript

MAX_BEATS = 20


def transcript_lines(transcript: Transcript) -> str:
    """LLM-format transcript: [wID] text lines."""
    return "\n".join(f"[{w.id}] {w.text}" for w in transcript.words)


async def _complete_json(llm: LLMProvider, system: str, user: str) -> Any:
    """One completion parsed as JSON (raises ValueError with the raw text)."""
    reply = await llm.complete(
        ChatRequest(
            messages=[
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ]
        )
    )
    try:
        return json.loads(reply.text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model did not return JSON: {reply.text[:200]!r} ({exc})") from None
