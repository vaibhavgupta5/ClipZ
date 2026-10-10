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


async def test_registry_uses_gemini_only_when_keyed() -> None:
    from app.providers.gemini import GeminiLLM
    from app.settings import Settings

    assert isinstance(provider_registry(Settings(GEMINI_API_KEY="k")).llm, GeminiLLM)


def test_registry_uses_local_libraries_when_present(tmp_path: object) -> None:
    from pathlib import Path

    from app.providers.local import LocalImageProvider
    from app.settings import Settings

    root = Path(str(tmp_path))
    (root / "images").mkdir()
    (root / "images" / "manifest.json").write_text("[]")
    registry = provider_registry(Settings(GEMINI_API_KEY="", ASSETS_DIR=str(root)))
    assert isinstance(registry.images, LocalImageProvider)
    assert isinstance(registry.music, FakeMusicProvider)


async def test_registry_defaults_to_fakes() -> None:
    from app.settings import Settings

    registry = provider_registry(Settings(GEMINI_API_KEY="", ASSETS_DIR="/nonexistent"))
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
        if path.parts[1] == "providers":
            continue  # providers/ owns vendor SDKs
        text = path.read_text()
        if any(f"import {mod}" in text for mod in banned):
            offenders.append(str(path))
    assert offenders == []
