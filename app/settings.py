"""Application settings (pydantic-settings). Every field must be documented in .env.example."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from the environment. See .env.example."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TIGER_DATABASE_URL: str = Field(
        default="postgresql://reelforge:reelforge@localhost:5433/reelforge",
        description="Tiger Data (Postgres+TimescaleDB) DSN; Tiger Cloud in .env, local for tests",
    )
    TIGER_SCHEMA: str = Field(default="public", description="Postgres schema (tests isolate)")
    TIGER_CLOUD_DATABASE_URL: str = Field(
        default="", description="Tiger Cloud DSN for the Docker stack (compose maps it)"
    )
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0", description="Redis DSN for Arq queues"
    )
    STORAGE_BACKEND: Literal["local", "s3"] = Field(
        default="local",
        description="Storage backend: local | s3 (AWS S3, DigitalOcean Spaces, Cloudflare R2, MinIO)",
    )
    S3_ENDPOINT_URL: str = Field(
        default="",
        description="S3 endpoint URL (e.g. https://nyc3.digitaloceanspaces.com or empty for AWS)",
    )
    S3_BUCKET_NAME: str = Field(default="", description="S3 bucket / Space name")
    S3_REGION: str = Field(default="us-east-1", description="S3 / Spaces region")
    S3_ACCESS_KEY_ID: str = Field(default="", description="S3 access key / Spaces key")
    S3_SECRET_ACCESS_KEY: str = Field(default="", description="S3 secret key / Spaces secret")
    S3_PUBLIC_URL_BASE: str = Field(
        default="",
        description="Optional public CDN / Spaces URL base (e.g. https://bucket.nyc3.cdn.digitaloceanspaces.com)",
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
        default="gemini-2.5-flash-lite", description="Gemini model (owner: 2.5 Flash-Lite only)"
    )
    GEMINI_FALLBACK_MODELS: str = Field(
        default="gemini-3.5-flash-lite",
        description="Comma-separated models tried in order when the primary's quota is exhausted",
    )
    GEMINI_THINKING_LEVEL: str = Field(
        default="", description="Gemini thinking level; empty = model default (required for 2.5)"
    )
    ELEVENLABS_API_KEY: str = Field(
        default="", description="ElevenLabs key (empty = generated SFX, voiceover, dubbing off)"
    )
    ELEVENLABS_PLAN: str = Field(
        default="free",
        description="free | paid. Free-plan output is personal use + credited (ADR-017)",
    )
    ELEVENLABS_VOICE_ID: str = Field(
        default="JBFqnCBsd6RMkjVDRZzb", description="Voiceover voice (premade 'George')"
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

    @property
    def elevenlabs_plan(self) -> Literal["free", "paid"]:
        """Plan tier for licence metadata; anything but 'paid' is treated as free."""
        return "paid" if self.ELEVENLABS_PLAN.strip().lower() == "paid" else "free"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
