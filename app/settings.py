"""Application settings (pydantic-settings). Every field must be documented in .env.example."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment. See .env.example."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    MONGODB_URL: str = Field(
        default="mongodb://localhost:27018", description="MongoDB connection string"
    )
    MONGODB_DB: str = Field(default="reelforge", description="MongoDB database name")
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0", description="Redis DSN for Arq queues"
    )
    UPLOAD_DIR: str = Field(
        default="./upload", description="Raw uploads (deleted once ingest succeeds)"
    )
    WORK_DIR: str = Field(
        default="./work", description="Masters, proxies, caches (project dir deleted after export)"
    )
    EDITED_DIR: str = Field(default="./editedVideo", description="Previews and final exports")
    MAX_UPLOAD_MB: int = Field(default=1024, ge=1, description="Upload size cap in MB")
    GEMINI_API_KEY: str = Field(default="", description="Gemini key (empty = fake LLM)")
    GEMINI_MODEL: str = Field(
        default="gemini-3.8-flash", description="Gemini model for the Director and analysis"
    )
    GEMINI_THINKING_LEVEL: str = Field(
        default="low", description="Gemini thinking level: minimal|low|medium|high"
    )
    WHISPER_MODEL: str = Field(
        default="small", description="faster-whisper model: tiny|base|small|medium|large-v3"
    )
    CHAT_PREVIEW_WAIT_S: int = Field(
        default=180, ge=0, description="How long a chat stream waits for its preview render"
    )
    ASSETS_DIR: str = Field(
        default="./assets",
        description="Local licensed asset libraries (music, gifs, images, memes)",
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
