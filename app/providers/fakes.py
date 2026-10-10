"""Deterministic fake providers for unit tests and local dev without keys."""

from collections.abc import Callable

from app.providers.base import (
    Candidate,
    ChatReply,
    ChatRequest,
    MemeTemplate,
    MusicSearchQuery,
    MusicTrack,
    SfxClip,
    VisualQuery,
)
from app.providers.tools import TextSink, ToolTurnReply, ToolTurnRequest


class FakeLLMProvider:
    """Scripted LLM: echoes the last user message (tests override per-case in Phase 5)."""

    provider_name = "fake-llm"

    async def complete(self, request: ChatRequest) -> ChatReply:
        last_user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        return ChatReply(text=f"[fake-llm] {last_user}", model="fake", tokens_used=7)


class FakeMusicProvider:
    """One deterministic licensed track per search."""

    provider_name = "fake-music"

    async def search(self, query: MusicSearchQuery) -> list[MusicTrack]:
        return [
            MusicTrack(
                id="fake:upbeat-bed-01",
                title=f"Fake {query.mood} bed",
                url="https://example.com/fake-bed.mp3",
                duration_s=float(query.duration_s),
                license="CC0-1.0",
                attribution_required=False,
            )
        ]

    async def download(self, track_id: str) -> bytes:
        return f"FAKE-MP3:{track_id}".encode()


class FakeImageProvider:
    """Deterministic licensed image candidates."""

    provider_name = "fake-image"

    async def search(self, query: VisualQuery) -> list[Candidate]:
        return [
            Candidate(
                id=f"fake-img:{i}",
                kind="image",
                source="fake-image",
                url=f"https://example.com/img{i}.jpg",
                width=1080,
                height=1920,
                license="CC0-1.0",
                attribution_required=False,
                tags=[query.query, query.tone],
            )
            for i in range(min(query.count, 3))
        ]


class FakeGifProvider:
    """Deterministic licensed GIF candidates."""

    provider_name = "fake-gif"

    async def search(self, query: VisualQuery) -> list[Candidate]:
        return [
            Candidate(
                id=f"fake-gif:{i}",
                kind="gif",
                source="fake-gif",
                url=f"https://example.com/anim{i}.gif",
                width=480,
                height=270,
                license="CC0-1.0",
                attribution_required=False,
                tags=[query.query],
            )
            for i in range(min(query.count, 3))
        ]


class FakeMemeTemplateProvider:
    """One built-in template with caption rendering."""

    provider_name = "fake-meme"

    async def list_templates(self, limit: int = 50) -> list[MemeTemplate]:
        return [
            MemeTemplate(
                id="fake:drake",
                name="Fake Drake",
                url="https://example.com/drake.jpg",
                license="CC0-1.0",
                attribution_required=False,
            )
        ][:limit]

    async def render_captioned(
        self, template_id: str, top_text: str, bottom_text: str
    ) -> Candidate:
        _ = (top_text, bottom_text)
        return Candidate(
            id=f"{template_id}:captioned",
            kind="meme",
            source="fake-meme",
            url=f"https://example.com/{template_id}-captioned.jpg",
            width=1080,
            height=1080,
            license="CC0-1.0",
            attribution_required=False,
        )


class FakeSfxProvider:
    """Deterministic SFX clips."""

    provider_name = "fake-sfx"

    async def search(self, hint: str, limit: int = 5) -> list[SfxClip]:
        return [
            SfxClip(
                id=f"fake-sfx:{hint}_{i}",
                name=f"Fake {hint} {i}",
                url=f"https://example.com/{hint}{i}.wav",
                duration_s=0.4,
                license="CC0-1.0",
                attribution_required=False,
            )
            for i in range(min(limit, 2))
        ]

    async def download(self, clip_id: str) -> bytes:
        return f"FAKE-WAV:{clip_id}".encode()


class ScriptedToolLLM:
    """ToolCallingLLM that replays scripted turns and records every request.

    Each script entry is a ToolTurnReply or a function of the request (to react to
    tool results). Running past the script ends the turn with no calls.
    """

    provider_name = "scripted"

    def __init__(
        self,
        script: "list[ToolTurnReply | Callable[[ToolTurnRequest], ToolTurnReply]]",
        model: str = "scripted",
    ) -> None:
        self.model = model
        self.script = list(script)
        self.requests: list[ToolTurnRequest] = []

    async def complete_with_tools(
        self, request: ToolTurnRequest, on_text: TextSink | None = None
    ) -> ToolTurnReply:
        """Pop the next scripted turn; stream its text to ``on_text``."""
        self.requests.append(request)
        step = self.script.pop(0) if self.script else ToolTurnReply(text="(script ended)")
        reply = step(request) if callable(step) else step
        if on_text is not None and reply.text:
            on_text(reply.text)
        return reply.model_copy(update={"model": reply.model or self.model})
