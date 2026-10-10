"""Integration test helper: wipe every row owned by an API key's user."""

import hashlib
import uuid

from sqlalchemy import delete, select

from app.db import create_engine
from app.models import (
    Analysis,
    Asset,
    Job,
    Message,
    Project,
    Render,
    TimelineVersion,
    User,
)
from app.models import Timeline as TimelineRow
from app.settings import get_settings


async def cleanup_user(api_key: str) -> None:
    """Delete the stub user and ALL their projects (children first)."""
    digest = hashlib.sha256(api_key.encode()).hexdigest()
    engine = create_engine(get_settings())
    try:
        async with engine.begin() as conn:
            user_ids = (
                (await conn.execute(select(User.id).where(User.api_key_hash == digest)))
                .scalars()
                .all()
            )
            for uid in user_ids:
                project_ids = (
                    (await conn.execute(select(Project.id).where(Project.user_id == uid)))
                    .scalars()
                    .all()
                )
                for pid in project_ids:
                    await _delete_project(conn, pid)
                await conn.execute(delete(User).where(User.id == uid))
    finally:
        await engine.dispose()


async def _delete_project(conn, project_id: uuid.UUID) -> None:  # type: ignore[no-untyped-def]
    tids = (
        (await conn.execute(select(TimelineRow.id).where(TimelineRow.project_id == project_id)))
        .scalars()
        .all()
    )
    for tid in tids:
        await conn.execute(delete(TimelineVersion).where(TimelineVersion.timeline_id == tid))
    for table in (Render, Message, TimelineRow, Job, Asset, Analysis):
        await conn.execute(delete(table).where(table.project_id == project_id))
    await conn.execute(delete(Project).where(Project.id == project_id))
