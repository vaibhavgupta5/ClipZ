"""Provider interface tests: fakes are schema-valid; registry resolves defaults."""

from app.providers.base import (
    GifProvider,
    ImageProvider,
    LLMProvider,
    MemeTemplateProvider,
    MusicProvider,
    SfxProvider,
)
from app.providers.fakes import (
    FakeGifProvider,
    FakeImageProvider,
    FakeLLMProvider,
    FakeMemeTemplateProvider,
    FakeMusicProvider,
    FakeSfxProvider,
)
from app.providers.registry import provider_registry


def test_fakes_satisfy_protocols() -> None:
    assert isinstance(FakeLLMProvider(), LLMProvider)
    assert isinstance(FakeMusicProvider(), MusicProvider)
    assert isinstance(FakeImageProvider(), ImageProvider)
    assert isinstance(FakeGifProvider(), GifProvider)
    assert isinstance(FakeMemeTemplateProvider(), MemeTemplateProvider)
    assert isinstance(FakeSfxProvider(), SfxProvider)


async def test_fake_llm_returns_structured_reply() -> None:
    from app.providers.base import ChatMessage, ChatRequest

    reply = await FakeLLMProvider().complete(
        ChatRequest(messages=[ChatMessage(role="user", content="hello")])
    )
    assert reply.text
    assert reply.tokens_used >= 0


async def test_fake_music_search_returns_licensed_tracks() -> None:
    from app.providers.base import MusicSearchQuery

    tracks = await FakeMusicProvider().search(MusicSearchQuery(mood="upbeat", duration_s=30))
    assert len(tracks) >= 1
    for track in tracks:
        assert track.id
        assert track.license
        assert isinstance(track.attribution_required, bool)


async def test_registry_defaults_to_fakes() -> None:
    registry = provider_registry()
    assert isinstance(registry.llm, FakeLLMProvider)
    assert isinstance(registry.music, FakeMusicProvider)
    assert isinstance(registry.images, FakeImageProvider)
    assert isinstance(registry.gifs, FakeGifProvider)
    assert isinstance(registry.memes, FakeMemeTemplateProvider)
    assert isinstance(registry.sfx, FakeSfxProvider)


def test_no_vendor_sdks_outside_providers() -> None:
    """Business logic must never import vendor SDKs directly (rule 8)."""
    import pathlib

    banned = ("anthropic", "google.generativeai", "google.genai", "openai", "boto3")
    offenders: list[str] = []
    for path in pathlib.Path("app").rglob("*.py"):
        if path.parts[1] == "providers" or path.name == "storage.py":
            continue  # providers/ owns vendor SDKs; storage.py owns boto3
        text = path.read_text()
        if any(f"import {mod}" in text for mod in banned):
            offenders.append(str(path))
    assert offenders == []
