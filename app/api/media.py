"""Serve stored files (previews, exports, proxies) to their owner: GET /media/<key>."""

import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import DbDep, UserDep, owned_project
from app.settings import get_settings
from app.storage import get_storage, validate_key

router = APIRouter()

_SERVABLE = {"renders", "projects"}  # raw uploads and shared caches are never served


@router.get("/media/{key:path}")
async def get_media(key: str, db: DbDep, user_id: UserDep) -> FileResponse:
    """Stream a file if its key belongs to a project the caller owns."""
    try:
        validate_key(key)
        head, project, _ = key.split("/", 2)
        project_id = uuid.UUID(project)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found") from None
    if head not in _SERVABLE:
        raise HTTPException(status_code=404, detail="not found")
    await owned_project(db, user_id, project_id)
    path = get_storage(get_settings()).path_for(key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)
