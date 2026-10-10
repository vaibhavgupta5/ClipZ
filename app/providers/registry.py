"""Provider wiring: names -> implementations. Phase 0 resolves everything to fakes."""

from dataclasses import dataclass

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
from app.settings import Settings, get_settings


@dataclass(frozen=True)
class ProviderRegistry:
    """All provider implementations for one process."""

    llm: LLMProvider
    music: MusicProvider
    images: ImageProvider
    gifs: GifProvider
    memes: MemeTemplateProvider
    sfx: SfxProvider


def provider_registry(settings: Settings | None = None) -> ProviderRegistry:
    """Build the registry. Non-'fake' names fail fast until real adapters land."""
    settings = settings or get_settings()
    if settings.MUSIC_PROVIDER != "fake":
        raise ValueError(
            f"unknown MUSIC_PROVIDER={settings.MUSIC_PROVIDER!r} (real adapters land in Phase 6)"
        )
    return ProviderRegistry(
        llm=FakeLLMProvider(),
        music=FakeMusicProvider(),
        images=FakeImageProvider(),
        gifs=FakeGifProvider(),
        memes=FakeMemeTemplateProvider(),
        sfx=FakeSfxProvider(),
    )
