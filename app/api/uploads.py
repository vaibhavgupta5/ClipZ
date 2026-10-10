"""Upload flow: presigned PUT -> complete (verify + enqueue ingest) -> SSE progress.

The API never proxies video bytes. Staging keys are project-bound so one
project cannot complete another project's upload.
"""

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.api.deps import SessionDep, UserDep, owned_project
from app.media.validate import MAX_UPLOAD_BYTES
from app.models import Job
from app.settings import get_settings
from app.storage import get_storage

router = APIRouter()

_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class UploadRequest(BaseModel):
    """Ask for a presigned upload URL."""

    filename: str = Field(description="Original filename (sanitized into the key)")
    content_type: str = Field(description="MIME type, must be video/*")
    size_bytes: int = Field(gt=0, description="Declared size (cap enforced)")


class CompleteRequest(BaseModel):
    """Confirm a finished client upload and start ingest."""

    key: str = Field(description="Staging key issued by POST .../uploads")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="Client-computed sha256")


def _safe_filename(filename: str) -> str:
    if not _FILENAME_RE.match(filename) or ".." in filename:
        raise ValueError(f"unsafe filename: {filename!r}")
    return filename


@router.post("/projects/{project_id}/uploads")
async def request_upload(
    project_id: uuid.UUID, body: UploadRequest, session: SessionDep, user_id: UserDep
) -> dict[str, object]:
    """Issue a presigned PUT URL for a project-bound staging key."""
    await owned_project(session, user_id, project_id)
    try:
        safe = _safe_filename(body.filename)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"errors": [{"message": str(exc)}]})  # type: ignore[return-value]
    if not body.content_type.startswith("video/"):
        return JSONResponse(
            status_code=422, content={"errors": [{"message": "content_type must be video/*"}]}
        )  # type: ignore[return-value]
    if body.size_bytes > MAX_UPLOAD_BYTES:
        return JSONResponse(
            status_code=422, content={"errors": [{"message": "size over the 1 GB cap"}]}
        )  # type: ignore[return-value]
    key = f"uploads/{project_id}/{uuid.uuid4().hex}/{safe}"
    url = get_storage(get_settings()).presigned_upload_url(key, body.content_type)
    return {"key": key, "upload_url": url}


@router.post("/projects/{project_id}/uploads/complete")
async def complete_upload(
    project_id: uuid.UUID,
    body: CompleteRequest,
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, object]:
    """Verify the staged object, create the ingest job (idempotent), enqueue it."""
    from app.workers.queue import enqueue_ingest

    await owned_project(session, user_id, project_id)
    prefix = f"uploads/{project_id}/"
    if not body.key.startswith(prefix):
        return JSONResponse(
            status_code=422,
            content={"errors": [{"message": "key was not issued for this project"}]},
        )  # type: ignore[return-value]
    store = get_storage(get_settings())
    try:
        store.stat(body.key)
    except KeyError:
        return JSONResponse(
            status_code=422, content={"errors": [{"message": "upload not found at key"}]}
        )  # type: ignore[return-value]
    idem = f"ingest:{project_id}:{body.sha256}"
    existing = await session.scalar(select(Job).where(Job.idempotency_key == idem))
    if existing is not None:
        return {"job_id": str(existing.id), "reused": True}
    job = Job(
        project_id=project_id, type="ingest", status="queued", idempotency_key=idem, payload={}
    )
    session.add(job)
    await session.flush()
    await enqueue_ingest(
        get_settings().REDIS_URL,
        {
            "job_id": str(job.id),
            "project_id": str(project_id),
            "storage_key": body.key,
            "sha256": body.sha256,
        },
    )
    await session.commit()
    return {"job_id": str(job.id), "reused": False}


@router.get("/projects/{project_id}/jobs/{job_id}/events")
async def job_events(
    project_id: uuid.UUID, job_id: uuid.UUID, session: SessionDep, user_id: UserDep
) -> EventSourceResponse:
    """SSE progress stream for one job (polls the jobs row until terminal)."""
    await owned_project(session, user_id, project_id)
    job = await session.get(Job, job_id)
    if job is None or job.project_id != project_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="job not found")

    async def stream() -> AsyncIterator[dict[str, Any]]:
        from app import db as db_module

        engine = db_module.create_engine(get_settings())
        try:
            factory = db_module.session_factory(engine)
            for _ in range(1200):  # 10 minutes at 0.5 s
                async with factory() as poll:
                    row = await poll.get(Job, job_id)
                    if row is None:
                        yield {"event": "failed", "data": '{"status": "failed"}'}
                        return
                    yield {
                        "event": "status",
                        "data": json.dumps(
                            {
                                "status": row.status,
                                "progress": row.progress,
                                "error": row.error,
                            }
                        ),
                    }
                    if row.status in ("done", "failed"):
                        return
                await asyncio.sleep(0.5)
        finally:
            await engine.dispose()

    return EventSourceResponse(stream())
