"""Upload flow: multipart POST -> ./upload/<project>/ -> ingest job -> SSE progress (ADR-010).

The file is streamed to disk in chunks (never held in memory), hashed on the fly,
and capped at MAX_UPLOAD_MB. Staging paths are project-bound and the filename is
sanitised, so one project cannot touch another project's upload.
"""

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app import db as db_ops
from app.api.deps import DbDep, UserDep, owned_project
from app.db import DuplicateKeyError
from app.models import Job
from app.settings import get_settings
from app.storage import get_storage

router = APIRouter()

_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CHUNK = 1024 * 1024


def _safe_filename(filename: str) -> str:
    if not _FILENAME_RE.match(filename) or ".." in filename:
        raise ValueError(f"unsafe filename: {filename!r} (use letters, digits, . _ -)")
    return filename


def _error(message: str, status: int = 422) -> JSONResponse:
    return JSONResponse(status_code=status, content={"errors": [{"message": message}]})


@router.post("/projects/{project_id}/uploads", response_model=None)
async def upload_video(
    project_id: uuid.UUID, file: UploadFile, db: DbDep, user_id: UserDep
) -> dict[str, object] | JSONResponse:
    """Save the upload to ./upload/<project>/, create the ingest job (idempotent), enqueue it."""
    from app.workers.queue import enqueue_ingest

    await owned_project(db, user_id, project_id)
    try:
        safe = _safe_filename(file.filename or "")
    except ValueError as exc:
        return _error(str(exc))
    if not (file.content_type or "").startswith("video/"):
        return _error("content type must be video/*")

    settings = get_settings()
    cap = settings.MAX_UPLOAD_MB * 1024 * 1024
    store = get_storage(settings)
    key = f"uploads/{project_id}/{uuid.uuid4().hex}/{safe}"
    path = store.path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    try:
        with path.open("wb") as out:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > cap:
                    raise ValueError(f"upload over the {settings.MAX_UPLOAD_MB} MB cap")
                digest.update(chunk)
                out.write(chunk)
    except ValueError as exc:
        path.unlink(missing_ok=True)
        return _error(str(exc), status=413)
    if size == 0:
        path.unlink(missing_ok=True)
        return _error("empty upload")

    sha = digest.hexdigest()
    idem = f"ingest:{project_id}:{sha}"
    existing = await db_ops.find_one(db, Job, {"idempotency_key": idem})
    if existing is not None:
        path.unlink(missing_ok=True)  # same bytes already ingested/ingesting
        return {"job_id": str(existing.id), "reused": True, "sha256": sha}
    try:
        job = await db_ops.insert(
            db, Job(project_id=project_id, type="ingest", idempotency_key=idem)
        )
    except DuplicateKeyError:
        path.unlink(missing_ok=True)
        raced = await db_ops.find_one(db, Job, {"idempotency_key": idem})
        assert raced is not None
        return {"job_id": str(raced.id), "reused": True, "sha256": sha}
    await enqueue_ingest(
        settings.REDIS_URL,
        {"job_id": str(job.id), "project_id": str(project_id), "storage_key": key, "sha256": sha},
    )
    return {"job_id": str(job.id), "reused": False, "sha256": sha}


@router.get("/projects/{project_id}/jobs/{job_id}/events")
async def job_events(
    project_id: uuid.UUID, job_id: uuid.UUID, db: DbDep, user_id: UserDep
) -> EventSourceResponse:
    """SSE progress stream for one job (polls the job document until terminal)."""
    await owned_project(db, user_id, project_id)
    job = await db_ops.get(db, Job, job_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(status_code=404, detail="job not found")

    async def stream() -> AsyncIterator[dict[str, Any]]:
        poll_db = db_ops.get_db()
        for _ in range(1200):  # 10 minutes at 0.5 s
            row = await db_ops.get(poll_db, Job, job_id)
            if row is None:
                yield {"event": "failed", "data": '{"status": "failed"}'}
                return
            yield {
                "event": "status",
                "data": json.dumps(
                    {"status": row.status, "progress": row.progress, "error": row.error}
                ),
            }
            if row.status in ("done", "failed"):
                return
            await asyncio.sleep(0.5)

    return EventSourceResponse(stream())
