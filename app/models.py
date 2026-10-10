"""Core data model: users, projects, assets, timelines, versions, messages, jobs, renders."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all ReelForge tables."""


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    """API consumer (stub auth: API key header until Phase 10 JWT)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    api_key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Project(Base):
    """One reel-editing project owned by a user."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="Untitled reel")
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="auto")
    caption_script: Mapped[str] = mapped_column(String(16), nullable=False, default="devanagari")
    created_at: Mapped[datetime] = _created_at()


class Asset(Base):
    """Any media file: raw upload, normalized master, proxy, music, insert, sfx."""

    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    license: Mapped[str] = mapped_column(String(64), nullable=False)
    attribution_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attribution_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(512), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Timeline(Base):
    """Editable EDL pointer: current_version moves on undo/redo."""

    __tablename__ = "timelines"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    style_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trend_pack: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TimelineVersion(Base):
    """Immutable EDL snapshot; undo/redo = pointer move on timelines.current_version."""

    __tablename__ = "timeline_versions"
    __table_args__ = (UniqueConstraint("timeline_id", "version", name="uq_timeline_version"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    timeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("timelines.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    edl: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Message(Base):
    """Chat history + tool calls per project."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list[object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _created_at()


class Job(Base):
    """Arq job record: ingest, analysis, render. Idempotent via idempotency_key."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Render(Base):
    """Preview/final render linked to an exact timeline hash."""

    __tablename__ = "renders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    timeline_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("timeline_versions.id"), nullable=False
    )
    timeline_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    output_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributions: Mapped[list[object]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = _created_at()
