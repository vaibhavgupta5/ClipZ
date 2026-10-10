"""Timeline CRUD: projects, ops, versions, undo/redo."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, UserDep, owned_project
from app.models import Project
from app.models import Timeline as TimelineRow
from app.timeline import ops as timeline_ops
from app.timeline.errors import OpError, TimelineInvalid
from app.timeline.schema import (
    CaptionConfig,
    Diff,
    MusicBed,
    Overlay,
    SfxHit,
    Timeline,
    VideoClip,
    Zoom,
)
from app.timeline.summary import summarize
from app.timeline.transcript import Transcript
from app.timeline.validate import ensure_valid
from app.timeline.versions import (
    get_current,
    list_versions,
    load_version,
    save_version,
    set_current,
)

router = APIRouter()


# --- Op request models (discriminated union; one variant per op) ---
class TrimOp(BaseModel):
    op: Literal["trim"] = "trim"
    start_word: str
    end_word: str
    pad_ms: int = 100


class SplitOp(BaseModel):
    op: Literal["split"] = "split"
    at_word: str


class DeleteRangeOp(BaseModel):
    op: Literal["delete_range"] = "delete_range"
    start_word: str
    end_word: str
    pad_ms: int = 100


class DeleteWordsOp(BaseModel):
    op: Literal["delete_words"] = "delete_words"
    word_ids: list[str] = Field(min_length=1)


class ReorderOp(BaseModel):
    op: Literal["reorder"] = "reorder"
    clip_ids: list[str] = Field(min_length=1)


class SetSpeedOp(BaseModel):
    op: Literal["set_speed"] = "set_speed"
    start_word: str
    end_word: str
    factor: float


class AddOverlayOp(BaseModel):
    op: Literal["add_overlay"] = "add_overlay"
    overlay: Overlay


class RemoveOverlayOp(BaseModel):
    op: Literal["remove_overlay"] = "remove_overlay"
    overlay_id: str


class AddZoomOp(BaseModel):
    op: Literal["add_zoom"] = "add_zoom"
    zoom: Zoom


class RemoveZoomOp(BaseModel):
    op: Literal["remove_zoom"] = "remove_zoom"
    zoom_id: str


class SetCaptionsOp(BaseModel):
    op: Literal["set_captions"] = "set_captions"
    config: CaptionConfig


class SetMusicOp(BaseModel):
    op: Literal["set_music"] = "set_music"
    bed: MusicBed | None = None


class AddSfxOp(BaseModel):
    op: Literal["add_sfx"] = "add_sfx"
    hit: SfxHit


class RemoveSfxOp(BaseModel):
    op: Literal["remove_sfx"] = "remove_sfx"
    sfx_id: str


OpRequest = Annotated[
    TrimOp
    | SplitOp
    | DeleteRangeOp
    | DeleteWordsOp
    | ReorderOp
    | SetSpeedOp
    | AddOverlayOp
    | RemoveOverlayOp
    | AddZoomOp
    | RemoveZoomOp
    | SetCaptionsOp
    | SetMusicOp
    | AddSfxOp
    | RemoveSfxOp,
    Field(discriminator="op"),
]


class CreateProjectRequest(BaseModel):
    """Create a project: timeline v1 now with transcript, else on analysis."""

    name: str = Field(default="Untitled reel", max_length=200)
    language: str = Field(default="auto")
    caption_script: str = Field(default="devanagari")
    transcript: Transcript | None = Field(default=None, description="Optional initial transcript")
    source_asset: str | None = Field(default=None, description="Raw asset ID for clip c1")
    source_duration_s: float | None = Field(default=None, gt=0, le=90)


def _apply(tl: Timeline, op: OpRequest) -> tuple[Timeline, Diff]:
    """Dispatch an op request to the pure op function (typed via match)."""
    match op:
        case TrimOp():
            return timeline_ops.trim(tl, op.start_word, op.end_word, op.pad_ms)
        case SplitOp():
            return timeline_ops.split(tl, op.at_word)
        case DeleteRangeOp():
            return timeline_ops.delete_range(tl, op.start_word, op.end_word, op.pad_ms)
        case DeleteWordsOp():
            return timeline_ops.delete_words(tl, op.word_ids)
        case ReorderOp():
            return timeline_ops.reorder(tl, op.clip_ids)
        case SetSpeedOp():
            return timeline_ops.set_speed(tl, op.start_word, op.end_word, op.factor)
        case AddOverlayOp():
            return timeline_ops.add_overlay(tl, op.overlay)
        case RemoveOverlayOp():
            return timeline_ops.remove_overlay(tl, op.overlay_id)
        case AddZoomOp():
            return timeline_ops.add_zoom(tl, op.zoom)
        case RemoveZoomOp():
            return timeline_ops.remove_zoom(tl, op.zoom_id)
        case SetCaptionsOp():
            return timeline_ops.set_captions(tl, op.config)
        case SetMusicOp():
            return timeline_ops.set_music(tl, op.bed)
        case AddSfxOp():
            return timeline_ops.add_sfx(tl, op.hit)
        case RemoveSfxOp():
            return timeline_ops.remove_sfx(tl, op.sfx_id)


def _timeline_response(tl: Timeline, version: int) -> dict[str, object]:
    return {
        "version": version,
        "timeline": tl.model_dump(mode="json"),
        "summary": summarize(tl),
    }


@router.post("/projects")
async def create_project(
    body: CreateProjectRequest,
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, object]:
    """Create project; timeline v1 now when a transcript is supplied, else on analysis."""
    project = Project(
        name=body.name, user_id=user_id, language=body.language, caption_script=body.caption_script
    )
    session.add(project)
    await session.flush()
    if body.transcript is None:
        await session.commit()
        return {"project_id": str(project.id), "timeline_id": None, "timeline": None}
    if body.source_asset is None or body.source_duration_s is None:
        raise HTTPException(
            status_code=422, detail="source_asset and source_duration_s required with transcript"
        )
    tl = Timeline(
        project_id=project.id,
        transcript=body.transcript,
        video_clips=[
            VideoClip(id="c1", asset=body.source_asset, src_in=0.0, src_out=body.source_duration_s)
        ],
    )
    ensure_valid(tl)
    row = TimelineRow(project_id=project.id, current_version=0)
    session.add(row)
    await session.flush()
    version, stamped = await save_version(session, row.id, tl, parent=None)
    await session.commit()
    return {
        "project_id": str(project.id),
        "timeline_id": str(row.id),
        **_timeline_response(stamped, version),
    }


@router.get("/projects/{project_id}/timeline")
async def get_timeline(
    project_id: uuid.UUID,
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, object]:
    """Current EDL + summary."""
    project = await owned_project(session, user_id, project_id)
    row = await _require_timeline(session, project)
    version = await get_current(session, row.id)
    tl = await load_version(session, row.id, version)
    return {
        "project_id": str(project.id),
        "timeline_id": str(row.id),
        **_timeline_response(tl, version),
    }


@router.post("/projects/{project_id}/ops")
async def apply_op(
    project_id: uuid.UUID,
    op: OpRequest,
    session: SessionDep,
    user_id: UserDep,
) -> JSONResponse:
    """Apply one op: validate, persist new version, return EDL + diff."""
    project = await owned_project(session, user_id, project_id)
    row = await _require_timeline(session, project)
    version = await get_current(session, row.id)
    tl = await load_version(session, row.id, version)
    try:
        new_tl, diff = _apply(tl, op)
        ensure_valid(new_tl)
    except (OpError, TimelineInvalid) as exc:
        return JSONResponse(status_code=422, content={"errors": _error_list(exc)})
    new_version, stamped = await save_version(session, row.id, new_tl, parent=version)
    await session.commit()
    return JSONResponse(
        status_code=200,
        content={
            "project_id": str(project.id),
            "diff": diff.model_dump(),
            **_timeline_response(stamped, new_version),
        },
    )


@router.get("/projects/{project_id}/versions")
async def list_timeline_versions(
    project_id: uuid.UUID,
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, object]:
    """All versions with parent links."""
    project = await owned_project(session, user_id, project_id)
    row = await _require_timeline(session, project)
    entries = await list_versions(session, row.id)
    return {
        "versions": [{"version": e.version, "parent": e.parent} for e in entries],
        "current": await get_current(session, row.id),
    }


@router.post("/projects/{project_id}/undo")
async def undo(
    project_id: uuid.UUID,
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, object]:
    """Pointer back one version."""
    project = await owned_project(session, user_id, project_id)
    row = await _require_timeline(session, project)
    version = max(1, await get_current(session, row.id) - 1)
    await set_current(session, row.id, version)
    await session.commit()
    tl = await load_version(session, row.id, version)
    return {"project_id": str(project.id), **_timeline_response(tl, version)}


@router.post("/projects/{project_id}/redo")
async def redo(
    project_id: uuid.UUID,
    session: SessionDep,
    user_id: UserDep,
) -> dict[str, object]:
    """Pointer forward along the linear chain (no-op past a branch)."""
    project = await owned_project(session, user_id, project_id)
    row = await _require_timeline(session, project)
    current = await get_current(session, row.id)
    entries = await list_versions(session, row.id)
    nxt = next(
        (e.version for e in sorted(entries, key=lambda e: e.version) if e.version > current), None
    )
    child = next((e for e in entries if e.version == nxt), None) if nxt else None
    version = child.version if child and child.parent == current else current
    await set_current(session, row.id, version)
    await session.commit()
    tl = await load_version(session, row.id, version)
    return {"project_id": str(project.id), **_timeline_response(tl, version)}


async def _timeline_row(session: AsyncSession, project: Project) -> TimelineRow | None:
    from sqlalchemy import select

    return await session.scalar(select(TimelineRow).where(TimelineRow.project_id == project.id))


async def _require_timeline(session: AsyncSession, project: Project) -> TimelineRow:
    from fastapi import HTTPException

    row = await _timeline_row(session, project)
    if row is None:
        raise HTTPException(status_code=404, detail="timeline not ready (analysis pending)")
    return row


def _error_list(exc: OpError | TimelineInvalid) -> list[dict[str, str]]:
    if isinstance(exc, TimelineInvalid):
        return [e.model_dump() for e in exc.errors]
    return [{"code": exc.code, "message": str(exc), "hint": exc.hint}]
