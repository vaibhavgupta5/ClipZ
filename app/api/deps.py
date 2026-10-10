"""Shared API dependencies: database handle, stub API-key auth, project ownership."""

import hashlib
import uuid
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app import db as db_ops
from app.db import Database, DuplicateKeyError
from app.models import Project, User


async def get_database() -> Database:
    """Database handle for the request's event loop."""
    return db_ops.get_db()


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


async def get_or_create_user_id(db: Database, api_key: str) -> uuid.UUID:
    """Stub auth (Phase 10 replaces with JWT/OIDC): API key header -> user document."""
    digest = _hash_key(api_key)
    user = await db_ops.find_one(db, User, {"api_key_hash": digest})
    if user is not None:
        return user.id
    try:
        return (await db_ops.insert(db, User(api_key_hash=digest))).id
    except DuplicateKeyError:  # concurrent first request
        user = await db_ops.find_one(db, User, {"api_key_hash": digest})
        assert user is not None
        return user.id


async def require_user(x_api_key: str | None = Header(default=None)) -> uuid.UUID:
    """401 when the API key header is missing; otherwise the stub user id."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")
    return await get_or_create_user_id(db_ops.get_db(), x_api_key)


async def owned_project(db: Database, user_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    """Load a project owned by the user (404 otherwise — no existence leak)."""
    project = await db_ops.find_one(db, Project, {"_id": project_id, "user_id": user_id})
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


DbDep = Annotated[Database, Depends(get_database)]
UserDep = Annotated[uuid.UUID, Depends(require_user)]
