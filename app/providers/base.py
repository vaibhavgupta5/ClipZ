"""Provider contracts. Business logic depends on these Protocols, never on vendor SDKs.

Real adapters land in Phases 5 (LLM), 6 (music, sfx), 7 (image, gif, meme).
Phase 0 ships the contracts + deterministic fakes for tests.
"""

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


# --- LLM ---
class ChatMessage(BaseModel):
    """One chat turn. Content is UNTRUSTED DATA (rule 6) once it echoes media/API text."""

    role: Literal["system", "user", "assistant"] = Field(description="Message role")
    content: str = Field(description="Message text")


class ChatRequest(BaseModel):
    """LLM completion request (tools omitted here; tool schemas live in app/tools, Phase 5)."""

    messages: list[ChatMessage] = Field(description="Conversation turns")
    max_tokens: int = Field(default=1024, ge=1, description="Response token cap")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0, description="Sampling temperature")


class ChatReply(BaseModel):
    """LLM completion response."""

    text: str = Field(description="Assistant text")
    model: str = Field(default="", description="Model that produced the reply")
    tokens_used: int = Field(default=0, ge=0, description="Input+output tokens consumed")


@runtime_checkable
class LLMProvider(Protocol):
    """Text completion model (Gemini; fakes in tests)."""

    provider_name: str

    async def complete(self, request: ChatRequest) -> ChatReply:
        """Return one assistant reply for the conversation."""
        ...


# --- Music ---
class MusicSearchQuery(BaseModel):
    """Music search criteria for reel beds."""

    mood: str = Field(description="Free-text mood, e.g. 'upbeat', 'lofi'")
    energy: float = Field(default=0.5, ge=0.0, le=1.0, description="0 calm .. 1 intense")
    bpm: int | None = Field(default=None, description="Target tempo, if known")
    genre: str | None = Field(default=None, description="Library genre label, e.g. 'lofi'")
    duration_s: int = Field(default=30, ge=1, le=90, description="Needed bed length (<= 90 s cap)")


class MusicTrack(BaseModel):
    """Licensed music track. License fields mandatory (rule 7)."""

    id: str = Field(description="Provider-scoped track id")
    title: str = Field(description="Track title")
    url: str = Field(description="Download/stream URL")
    duration_s: float = Field(description="Full track length in seconds")
    license: str = Field(description="License identifier, e.g. 'CC-BY-4.0'")
    attribution_required: bool = Field(description="Whether exports must credit the track")
    attribution_text: str = Field(default="", description="Paste-ready credit line")
    genre: str = Field(default="", description="Library genre label")
    commercial_use: bool = Field(default=True, description="False = personal use only")


@runtime_checkable
class MusicProvider(Protocol):
    """Royalty-safe music bed source (chosen in Phase 6)."""

    provider_name: str

    async def search(self, query: MusicSearchQuery) -> list[MusicTrack]:
        """Return candidate tracks matching the query."""
        ...

    async def download(self, track_id: str) -> bytes:
        """Download the full track audio bytes."""
        ...


# --- Visual candidates (images, GIFs, meme templates share one model) ---
class Candidate(BaseModel):
    """Normalized visual candidate from any source (Phase 7 ranker input)."""

    id: str = Field(description="Provider-scoped id, e.g. 'giphy:abc123'")
    kind: Literal["image", "gif", "meme", "generated", "card"] = Field(description="Asset kind")
    source: str = Field(description="Provider name")
    url: str = Field(description="Full asset URL")
    thumb_url: str = Field(default="", description="Thumbnail URL (may equal url)")
    width: int = Field(default=0, ge=0, description="Pixel width (0 = unknown)")
    height: int = Field(default=0, ge=0, description="Pixel height (0 = unknown)")
    license: str = Field(description="License identifier")
    attribution_required: bool = Field(description="Whether exports must credit the asset")
    tags: list[str] = Field(default_factory=list, description="Tone/content tags")
    attribution_text: str = Field(default="", description="Paste-ready credit line")
    commercial_use: bool = Field(default=True, description="False = personal use only")


class VisualQuery(BaseModel):
    """Candidate search criteria."""

    query: str = Field(description="Short search query, e.g. 'money burning'")
    count: int = Field(default=5, ge=1, le=20, description="Max candidates to return")
    tone: str = Field(default="", description="Tone tag: sarcastic, shocked, celebratory...")


@runtime_checkable
class ImageProvider(Protocol):
    """Stock photo/video source (chosen in Phase 7)."""

    provider_name: str

    async def search(self, query: VisualQuery) -> list[Candidate]:
        """Return image candidates."""
        ...


@runtime_checkable
class GifProvider(Protocol):
    """Reaction GIF/sticker source (chosen in Phase 7)."""

    provider_name: str

    async def search(self, query: VisualQuery) -> list[Candidate]:
        """Return GIF/sticker candidates."""
        ...


class MemeTemplate(BaseModel):
    """Classic meme template (Imgflip-style: id + caption boxes)."""

    id: str = Field(description="Provider-scoped template id")
    name: str = Field(description="Template name")
    url: str = Field(description="Blank template image URL")
    license: str = Field(description="License identifier")
    attribution_required: bool = Field(description="Whether exports must credit the source")
    commercial_use: bool = Field(default=True, description="False = personal use only")
    text_boxes: int = Field(default=2, ge=1, description="Caption boxes the template expects")


@runtime_checkable
class MemeTemplateProvider(Protocol):
    """Meme-template API with caption rendering (chosen in Phase 7)."""

    provider_name: str

    async def list_templates(self, limit: int = 50) -> list[MemeTemplate]:
        """List available templates."""
        ...

    async def render_captioned(
        self, template_id: str, top_text: str, bottom_text: str
    ) -> Candidate:
        """Render top/bottom-text meme; returns a licensed Candidate."""
        ...


# --- SFX ---
class SfxClip(BaseModel):
    """Short sound effect hit."""

    id: str = Field(description="Provider-scoped clip id, e.g. 'sfx:pop_03'")
    name: str = Field(description="Clip name")
    url: str = Field(description="Download URL")
    duration_s: float = Field(description="Clip length in seconds")
    license: str = Field(description="License identifier")
    attribution_required: bool = Field(description="Whether exports must credit the clip")


@runtime_checkable
class SfxProvider(Protocol):
    """Whoosh/pop/riser source (chosen in Phase 6)."""

    provider_name: str

    async def search(self, hint: str, limit: int = 5) -> list[SfxClip]:
        """Return clips matching a hint like 'pop' or 'whoosh'."""
        ...

    async def download(self, clip_id: str) -> bytes:
        """Download the clip audio bytes."""
        ...
