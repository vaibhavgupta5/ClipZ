"""Provider wiring: Gemini for LLM work when keyed; local libraries land in Phase 6/7."""

from dataclasses import dataclass
from pathlib import Path

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
from app.providers.gemini import GeminiLLM
from app.providers.local import (
    LocalGifProvider,
    LocalImageProvider,
    LocalMemeTemplateProvider,
    LocalMusicProvider,
)
from app.providers.tools import ToolCallingLLM
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
    """Gemini when GEMINI_API_KEY is set; local libraries when their manifests exist; else fakes."""
    settings = settings or get_settings()
    llm: GeminiLLM | FakeLLMProvider = FakeLLMProvider()
    if settings.GEMINI_API_KEY:
        llm = _gemini(settings)
    root = Path(settings.ASSETS_DIR)

    def has(lib: str) -> bool:
        return (root / lib / "manifest.json").is_file()

    return ProviderRegistry(
        llm=llm,
        music=LocalMusicProvider(root) if has("music") else FakeMusicProvider(),
        images=LocalImageProvider(root) if has("images") else FakeImageProvider(),
        gifs=LocalGifProvider(root) if has("gifs") else FakeGifProvider(),
        memes=(
            LocalMemeTemplateProvider(root, Path(settings.WORK_DIR) / "cache" / "memes")
            if has("memes")
            else FakeMemeTemplateProvider()
        ),
        sfx=FakeSfxProvider(),  # no local SFX pack yet (Phase 6)
    )


def director_llm(settings: Settings | None = None) -> ToolCallingLLM:
    """The Director's tool-calling model (Gemini). Raises when no key is configured."""
    settings = settings or get_settings()
    return _gemini(settings)


def _gemini(settings: Settings) -> GeminiLLM:
    fallbacks = tuple(m.strip() for m in settings.GEMINI_FALLBACK_MODELS.split(",") if m.strip())
    return GeminiLLM(
        settings.GEMINI_API_KEY,
        settings.GEMINI_MODEL,
        settings.GEMINI_THINKING_LEVEL,
        fallback_models=fallbacks,
    )
