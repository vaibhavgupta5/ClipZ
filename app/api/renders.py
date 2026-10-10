"""Render jobs: enqueue preview/final renders, poll status, fetch output URL."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db as db_ops
from app.api.deps import DbDep, UserDep, owned_project
from app.api.timelines import _require_timeline
from app.models import Render as RenderRow
from app.render.errors import RenderError
from app.services.renders import request_render as create_render
from app.timeline.versions import get_current

router = APIRouter()


class RenderRequest(BaseModel):
    """Enqueue a render of a timeline version."""

    profile: str = Field(description="preview | final")
    version: int | None = Field(default=None, description="Timeline version (default: current)")


@router.post("/projects/{project_id}/render")
async def request_render(
    project_id: uuid.UUID, body: RenderRequest, db: DbDep, user_id: UserDep
) -> dict[str, object]:
    """Validate, create Render + Job documents, enqueue the render task."""
    project = await owned_project(db, user_id, project_id)
    row = await _require_timeline(db, project)
    version = body.version or await get_current(db, row.id)
    try:
        render, job = await create_render(db, project.id, row.id, version, body.profile)
    except (RenderError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {
        "render_id": str(render.id),
        "job_id": str(job.id),
        "timeline_hash": render.timeline_hash,
    }


@router.get("/projects/{project_id}/renders/{render_id}")
async def get_render(
    project_id: uuid.UUID, render_id: uuid.UUID, db: DbDep, user_id: UserDep
) -> dict[str, object]:
    """Render status + output URL + attributions."""
    await owned_project(db, user_id, project_id)
    render = await db_ops.get(db, RenderRow, render_id)
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
