"""Render requests shared by the HTTP API and the Director workspace."""

import hashlib
import json
import uuid

from app import db as db_ops
from app.db import Database
from app.models import Job, Render, TimelineVersion
from app.render.render import profile_for
from app.settings import get_settings
from app.timeline.schema import Timeline
from app.workers.queue import enqueue_render


def timeline_hash(tl: Timeline) -> str:
    """16-hex content hash of an EDL (stable key order)."""
    return hashlib.sha256(
        json.dumps(tl.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()[:16]


async def request_render(
    db: Database, project_id: uuid.UUID, timeline_id: uuid.UUID, version: int, profile: str
) -> tuple[Render, Job]:
    """Create Render + Job documents for one version and enqueue the render task.

    Raises RenderError for an unknown profile, ValueError for a missing version.
    """
    profile_for(profile)
    row = await db_ops.find_one(
        db, TimelineVersion, {"timeline_id": timeline_id, "version": version}
    )
    if row is None:
        raise ValueError(f"timeline has no version {version}")
    tl = Timeline.model_validate(row.edl)
    render = await db_ops.insert(
        db,
        Render(
            project_id=project_id,
            timeline_version_id=row.id,
            timeline_hash=timeline_hash(tl),
            profile=profile,
        ),
    )
    job = await db_ops.insert(
        db,
        Job(
            project_id=project_id,
            type="render",
            idempotency_key=f"render:{render.id}",
            payload={"render_id": str(render.id)},
        ),
    )
    await enqueue_render(
        get_settings().REDIS_URL, {"job_id": str(job.id), "render_id": str(render.id)}
    )
    return render, job
