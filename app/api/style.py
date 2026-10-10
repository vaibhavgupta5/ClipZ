"""My style: draft a Director prompt in the way the user edits their reels (ADR-021).

GET /projects/{id}/style-prompt   learn from the user's OTHER edited reels and return the
                                  habits found plus a prompt the user reviews before sending

Nothing is edited here: the studio shows the draft, and the user sends it (as written or
changed) through the normal chat endpoint.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db as db_ops
from app.api.deps import DbDep, UserDep, owned_project
from app.db import Database
from app.models import Message, Project, Render
from app.models import Timeline as TimelineRow
from app.providers.local import AssetCatalog
from app.settings import get_settings
from app.style.learn import Habit, StyleSample, learn_style, style_prompt
from app.timeline.schema import Timeline
from app.timeline.versions import load_version

router = APIRouter()

MAX_SAMPLES = 20  # newest edited reels learned from


class StylePromptOut(BaseModel):
    """Learned habits and the drafted prompt (None when there is nothing to learn from)."""

    samples: int = Field(description="Reels learned from")
    exported: int = Field(description="How many of them were exported (they weigh double)")
    habits: list[Habit] = Field(description="What you usually do, in Director order")
    prompt: str | None = Field(description="Draft Director message; review before sending")
    note: str = Field(description="One line for the studio")


async def _timeline(db: Database, row: TimelineRow) -> Timeline | None:
    try:
        return await load_version(db, row.id, row.current_version)
    except ValueError:
        return None


async def _tools_run(db: Database, project_id: uuid.UUID) -> frozenset[str]:
    """Names of the tools the Director called in this project's chat turns."""
    rows = await db_ops.find(db, Message, {"project_id": project_id, "role": "assistant"})
    return frozenset(
        str(call["name"])
        for row in rows
        for call in row.tool_calls or []
        if isinstance(call, dict) and isinstance(call.get("name"), str)
    )


def _genre(catalog: AssetCatalog, tl: Timeline) -> str | None:
    found = catalog.get(tl.music.asset) if tl.music is not None else None
    return found[1].genre or None if found is not None else None


async def gather_samples(
    db: Database, user_id: uuid.UUID, exclude: uuid.UUID, catalog: AssetCatalog
) -> list[StyleSample]:
    """The user's other reels with at least one kept edit, newest first."""
    projects = await db_ops.find(db, Project, {"user_id": user_id}, sort=[("created_at", -1)])
    samples: list[StyleSample] = []
    for project in projects:
        if project.id == exclude:
            continue  # its own edits would only be re-applied
        row = await db_ops.find_one(db, TimelineRow, {"project_id": project.id})
        if row is None or row.current_version <= 1:
            continue  # not analysed yet, or no edit kept (v1 is the raw upload)
        tl = await _timeline(db, row)
        if tl is None:
            continue
        final = await db_ops.find_one(
            db, Render, {"project_id": project.id, "profile": "final", "status": "done"}
        )
        samples.append(
            StyleSample(
                timeline=tl,
                exported=final is not None,
                tools=await _tools_run(db, project.id),
                music_genre=_genre(catalog, tl),
            )
        )
        if len(samples) >= MAX_SAMPLES:
            break
    return samples


@router.get("/projects/{project_id}/style-prompt", response_model=StylePromptOut)
async def get_style_prompt(project_id: uuid.UUID, db: DbDep, user_id: UserDep) -> StylePromptOut:
    """Draft a Director prompt from how the user edited their other reels."""
    project = await owned_project(db, user_id, project_id)
    row = await db_ops.find_one(db, TimelineRow, {"project_id": project.id})
    target = await _timeline(db, row) if row is not None else None
    if target is None:
        raise HTTPException(status_code=409, detail="the video is still being analysed")
    settings = get_settings()
    catalog = AssetCatalog(settings.ASSETS_DIR, Path(settings.WORK_DIR) / "cache" / "memes")
    samples = await gather_samples(db, user_id, project.id, catalog)
    profile = learn_style(samples, target.duration_s)
    prompt = style_prompt(profile)
    if prompt is None:
        note = (
            "Nothing to learn from yet: edit a reel or two (and export the ones you like), "
            "then try again."
            if not samples
            else f"Your {len(samples)} edited reel(s) share no habit yet."
        )
    else:
        note = (
            f"Learned from {profile.samples} reel(s), {profile.exported} exported "
            "(exported reels count double). Review and edit before sending."
        )
    return StylePromptOut(
        samples=profile.samples,
        exported=profile.exported,
        habits=profile.habits,
        prompt=prompt,
        note=note,
    )
