"""Application settings (pydantic-settings). Every field must be documented in .env.example."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment. See .env.example."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://reelforge:reelforge@localhost:5432/reelforge",
        description="Async SQLAlchemy DSN for Postgres+pgvector",
    )
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0", description="Redis DSN for Arq queues"
    )
    S3_ENDPOINT: str = Field(
        default="http://localhost:9000", description="S3-compatible endpoint (MinIO local)"
    )
    S3_BUCKET: str = Field(default="reelforge", description="Object-storage bucket")
    S3_ACCESS_KEY: str = Field(default="minioadmin", description="Object-storage access key")
    S3_SECRET_KEY: str = Field(
        default="reelforge-dev-secret-key",  # dev-only; must be >= 16 chars for Garage
        description="Object-storage secret key",
    )
    S3_REGION: str = Field(default="us-east-1", description="Object-storage region")
    ANTHROPIC_API_KEY: str = Field(
        default="", description="Claude provider key (empty = unavailable)"
    )
    GEMINI_API_KEY: str = Field(default="", description="Gemini provider key (empty = unavailable)")
    MUSIC_PROVIDER: str = Field(
        default="fake", description="MusicProvider adapter name (chosen in Phase 6)"
    )
    MUSIC_API_KEY: str = Field(default="", description="Music provider API key")
    GIF_PROVIDER_KEY: str = Field(
        default="", description="GIF/sticker provider key (chosen in Phase 7)"
    )
    STOCK_IMAGE_KEY: str = Field(
        default="", description="Stock photo/video provider key (chosen in Phase 7)"
    )
    MEME_PROVIDER_KEY: str = Field(
        default="", description="Meme-template provider key (chosen in Phase 7)"
    )
    IMAGEGEN_KEY: str = Field(
        default="", description="AI image-gen provider key (fallback, Phase 7)"
    )
    MAX_VIDEO_SECONDS: int = Field(
        default=90, description="Hard cap on reel duration (locked decision 1)"
    )
    DEFAULT_CAPTION_SCRIPT: str = Field(
        default="devanagari", description="devanagari | roman caption script for Hindi"
    )
    GPU_WORKER_URL: str = Field(
        default="", description="GPU worker base URL (empty = CPU fallbacks)"
    )
    SENTRY_DSN: str = Field(default="", description="Sentry DSN (empty = disabled)")
    OTEL_EXPORTER_OTLP_ENDPOINT: str = Field(
        default="", description="OTLP endpoint (empty = no-op tracer)"
    )
    LOG_LEVEL: str = Field(default="INFO", description="Root log level")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
