"""Shared API dependencies: DB sessions, stub API-key auth, project ownership."""

import hashlib
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db as db_module
from app.models import Project, User
from app.settings import get_settings


async def get_session() -> AsyncIterator[AsyncSession]:
    """Per-request session on a fresh engine.

    A fresh engine per request (not a global) keeps event-loop affinity correct
    under both uvicorn and function-scoped test loops. Lifespan pooling is Phase-10 work.
    """
    engine = db_module.create_engine(get_settings())
    try:
        factory = db_module.session_factory(engine)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


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
    engine = db_module.create_engine(get_settings())
    try:
        factory = db_module.session_factory(engine)
        async with factory() as session:
            return await get_or_create_user_id(session, x_api_key)
    finally:
        await engine.dispose()


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


SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[uuid.UUID, Depends(require_user)]
