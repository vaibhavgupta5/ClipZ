"""Shared API dependencies: DB sessions, stub API-key auth, project ownership."""

import hashlib
import uuid
from collections.abc import AsyncIterator

from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app import db as db_module
from app.models import Project, User
from app.settings import Settings, get_settings

_engine: AsyncEngine | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Process-wide cached async engine."""
    global _engine
    if _engine is None:
        _engine = db_module.create_engine(settings or get_settings())
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """Per-request session (routes commit explicitly)."""
    factory = db_module.session_factory(get_engine())
    async with factory() as session:
        yield session


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


async def get_or_create_user_id(session: AsyncSession, api_key: str) -> uuid.UUID:
    """Stub auth (Phase 10 replaces with JWT/OIDC): API key header -> user row."""
    digest = _hash_key(api_key)
    user_id = await session.scalar(select(User.id).where(User.api_key_hash == digest))
    if user_id is None:
        user = User(api_key_hash=digest)
        session.add(user)
        await session.flush()
        await session.commit()
        return user.id
    return user_id


async def require_user(x_api_key: str | None = Header(default=None)) -> uuid.UUID:
    """401 when the API key header is missing; otherwise the stub user id."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")
    factory = db_module.session_factory(get_engine())
    async with factory() as session:
        return await get_or_create_user_id(session, x_api_key)


async def owned_project(
    session: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    """Load a project owned by the user (404 otherwise — no existence leak)."""
    project = await session.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project
