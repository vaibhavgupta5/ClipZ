"""Data model: one pydantic class per Tiger Data table (ADR-016).

``collection`` is the table name; app/sql/*.sql defines the columns (same names).
``id`` also answers to ``_id`` in query filters. Telemetry tables are hypertables.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(UTC)


class Doc(BaseModel):
    """Base document: UUID ``_id`` + created_at."""

    model_config = ConfigDict(populate_by_name=True, validate_assignment=True)
    collection: ClassVar[str] = ""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, alias="_id")
    created_at: datetime = Field(default_factory=_now)


class User(Doc):
    """API consumer (stub auth: API key header until Phase 10 JWT)."""

    collection: ClassVar[str] = "users"
    api_key_hash: str


class Project(Doc):
    """One reel-editing project owned by a user."""

    collection: ClassVar[str] = "projects"
    user_id: uuid.UUID
    name: str = "Untitled reel"
    language: str = "auto"
    caption_script: str = "devanagari"


class Asset(Doc):
    """Any media file: raw upload, normalized master, proxy, music, insert, sfx."""

    collection: ClassVar[str] = "assets"
    project_id: uuid.UUID
    kind: str
    storage_key: str
    sha256: str
    source_sha: str | None = None
    source: str
    license: str
    attribution_required: bool = False
    attribution_text: str | None = None
    commercial_use: bool = True
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None


class Timeline(Doc):
    """Editable EDL pointer: current_version moves on undo/redo."""

    collection: ClassVar[str] = "timelines"
    project_id: uuid.UUID
    current_version: int = 1
    style_id: str | None = None
    trend_pack: str | None = None
    updated_at: datetime = Field(default_factory=_now)


class TimelineVersion(Doc):
    """Immutable EDL snapshot; unique (timeline_id, version)."""

    collection: ClassVar[str] = "timeline_versions"
    timeline_id: uuid.UUID
    version: int
    edl: dict[str, Any]
    parent_version: int | None = None


class Message(Doc):
    """Chat history + tool calls per project."""

    collection: ClassVar[str] = "messages"
    project_id: uuid.UUID
    role: str
    content: str
    tool_calls: list[Any] | None = None


class Job(Doc):
    """Arq job record: ingest, analysis, render. Idempotent via idempotency_key."""

    collection: ClassVar[str] = "jobs"
    project_id: uuid.UUID | None = None
    type: str
    status: str = "queued"
    progress: float = 0.0
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    updated_at: datetime = Field(default_factory=_now)


class Render(Doc):
    """Preview/final render linked to an exact timeline hash."""

    collection: ClassVar[str] = "renders"
    project_id: uuid.UUID
    timeline_version_id: uuid.UUID
    timeline_hash: str = ""
    profile: str
    status: str = "queued"
    output_url: str | None = None
    output_key: str | None = None
    attributions: list[Any] = Field(default_factory=list)


class Analysis(Doc):
    """Cached AnalysisBundle pointer: content-hash keyed, shared across projects."""

    collection: ClassVar[str] = "analyses"
    project_id: uuid.UUID
    source_sha: str
    analyzer: str
    version: str
    params_hash: str
    bundle_key: str


class AgentRun(Doc):
    """One Director run (hypertable): prompt hash, tools, latency, tokens, cost."""

    collection: ClassVar[str] = "agent_runs"
    project_id: uuid.UUID
    model: str
    prompt_hash: str
    tools: list[str] = Field(default_factory=list)
    stopped: str = ""
    iterations: int = 0
    latency_ms: int = 0
    first_token_ms: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0


class ToolCallLog(Doc):
    """One Director tool call (hypertable)."""

    collection: ClassVar[str] = "tool_calls"
    run_id: uuid.UUID | None = None
    project_id: uuid.UUID
    tool: str
    ok: bool
    error_code: str | None = None
    latency_ms: int = 0


class PipelineStage(Doc):
    """One finished ingest/analyze/render stage (hypertable)."""

    collection: ClassVar[str] = "pipeline_stages"
    project_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    stage: str
    ok: bool = True
    duration_ms: int
    media_s: float = 0.0


class RenderProgress(Doc):
    """One render progress tick (hypertable)."""

    collection: ClassVar[str] = "render_progress"
    render_id: uuid.UUID
    project_id: uuid.UUID | None = None
    profile: str
    progress: float
