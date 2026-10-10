"""Render jobs: enqueue preview/final renders, poll status, fetch output URL."""

import hashlib
import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import SessionDep, UserDep, owned_project
from app.api.timelines import _require_timeline
from app.models import Job, TimelineVersion
from app.models import Render as RenderRow
from app.render.errors import RenderError
from app.render.render import profile_for
from app.settings import get_settings
from app.timeline.versions import get_current, load_version
from app.workers.queue import enqueue_render

router = APIRouter()


class RenderRequest(BaseModel):
    """Enqueue a render of a timeline version."""

    profile: str = Field(description="preview | final")
    version: int | None = Field(default=None, description="Timeline version (default: current)")


@router.post("/projects/{project_id}/render")
async def request_render(
    project_id: uuid.UUID, body: RenderRequest, session: SessionDep, user_id: UserDep
) -> dict[str, object]:
    """Validate, create Render + Job rows, enqueue the render task."""
    project = await owned_project(session, user_id, project_id)
    try:
        profile_for(body.profile)
    except RenderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    row = await _require_timeline(session, project)
    version = body.version or await get_current(session, row.id)
    tl = await load_version(session, row.id, version)
    timeline_hash = hashlib.sha256(
        json.dumps(tl.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()[:16]

    version_id = await session.scalar(
        select(TimelineVersion.id).where(
            TimelineVersion.timeline_id == row.id, TimelineVersion.version == version
        )
    )
    render = RenderRow(
        project_id=project.id,
        timeline_version_id=version_id,
        timeline_hash=timeline_hash,
        profile=body.profile,
        status="queued",
    )
    session.add(render)
    await session.flush()
    job = Job(
        project_id=project.id,
        type="render",
        status="queued",
        idempotency_key=f"render:{render.id}",
        payload={"render_id": str(render.id)},
    )
    session.add(job)
    await session.flush()
    await enqueue_render(
        get_settings().REDIS_URL, {"job_id": str(job.id), "render_id": str(render.id)}
    )
    await session.commit()
    return {"render_id": str(render.id), "job_id": str(job.id), "timeline_hash": timeline_hash}


@router.get("/projects/{project_id}/renders/{render_id}")
async def get_render(
    project_id: uuid.UUID, render_id: uuid.UUID, session: SessionDep, user_id: UserDep
) -> dict[str, object]:
    """Render status + output URL + attributions."""
    await owned_project(session, user_id, project_id)
    render = await session.get(RenderRow, render_id)
    if render is None or render.project_id != project_id:
        raise HTTPException(status_code=404, detail="render not found")
    return {
        "render_id": str(render.id),
        "status": render.status,
        "profile": render.profile,
        "timeline_hash": render.timeline_hash,
        "output_url": render.output_url,
        "attributions": render.attributions,
    }
