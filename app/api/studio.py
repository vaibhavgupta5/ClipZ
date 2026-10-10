"""Studio: the web app page plus the small read APIs and brainstorm chat it needs.

GET  /app                              the single-page studio (static HTML, no build step)
GET  /projects                         the caller's projects, newest first, with status
GET  /projects/{id}/renders            previews/exports, newest first
POST /projects/{id}/brainstorm         talk the edit through with the Director (no edits);
                                       ends with a suggested final prompt to send
"""

import re
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import db as db_ops
from app.agent.context import project_data
from app.agent.workspace import latest_bundle
from app.api.deps import DbDep, UserDep, owned_project
from app.models import Job, Project, Render
from app.models import Timeline as TimelineRow
from app.providers.base import ChatMessage, ChatRequest, LLMProvider
from app.providers.gemini import ProviderUnavailable
from app.providers.registry import provider_registry
from app.settings import get_settings
from app.storage import get_storage
from app.timeline.versions import get_current, load_version

router = APIRouter()

PAGE = Path(__file__).resolve().parent.parent / "web" / "index.html"
BRAINSTORM = (
    (Path(__file__).resolve().parent.parent / "agent" / "prompts" / "brainstorm.txt")
    .read_text(encoding="utf-8")
    .strip()
)
FINAL_RE = re.compile(r"^\s*FINAL PROMPT:\s*(.+)$", re.MULTILINE | re.DOTALL)


@router.get("/app", include_in_schema=False)
async def studio_page() -> FileResponse:
    """The studio web app (it asks for the API key and keeps it in the browser)."""
    return FileResponse(PAGE, media_type="text/html")


@router.get("/projects")
async def list_projects(db: DbDep, user_id: UserDep) -> dict[str, object]:
    """Projects with a coarse status: new -> processing -> ready (timeline exists)."""
    rows = await db_ops.find(db, Project, {"user_id": user_id}, sort=[("created_at", -1)])
    out = []
    for p in rows:
        timeline = await db_ops.find_one(db, TimelineRow, {"project_id": p.id})
        if timeline is not None:
            status = "ready"
        else:
            jobs = await db_ops.find(db, Job, {"project_id": p.id}, sort=[("created_at", -1)])
            status = (
                "failed" if jobs and jobs[0].status == "failed" else "processing" if jobs else "new"
            )
        out.append(
            {
                "project_id": str(p.id),
                "name": p.name,
                "status": status,
                "created_at": p.created_at.isoformat(),
            }
        )
    return {"projects": out}


@router.get("/projects/{project_id}/renders")
async def list_renders(project_id: uuid.UUID, db: DbDep, user_id: UserDep) -> dict[str, object]:
    """Renders of a project, newest first (url is served by GET /media/...)."""
    await owned_project(db, user_id, project_id)
    rows = await db_ops.find(db, Render, {"project_id": project_id}, sort=[("created_at", -1)])
    return {
        "renders": [
            {
                "render_id": str(r.id),
                "profile": r.profile,
                "status": r.status,
                "url": r.output_url,
                "credits": r.attributions,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


class BrainstormTurn(BaseModel):
    """One message of the brainstorm (client keeps the history)."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class BrainstormBody(BaseModel):
    """Brainstorm history, oldest first; the last turn is the user's."""

    messages: list[BrainstormTurn] = Field(min_length=1, max_length=24)


def get_brainstorm_llm() -> LLMProvider:
    """Plain-completion model for brainstorming (overridden in tests)."""
    return provider_registry(get_settings()).llm


BrainstormLlm = Annotated[LLMProvider, Depends(get_brainstorm_llm)]


@router.post("/projects/{project_id}/brainstorm")
async def brainstorm(
    project_id: uuid.UUID,
    body: BrainstormBody,
    db: DbDep,
    user_id: UserDep,
    llm: BrainstormLlm,
) -> dict[str, object]:
    """Discuss the edit with the Director (no tools, nothing changes). When the idea is clear
    the reply ends with 'FINAL PROMPT: ...', returned separately for the Send button."""
    project = await owned_project(db, user_id, project_id)
    if body.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail="the last message must be the user's")
    data = await _project_context(db, project)
    request = ChatRequest(
        messages=[
            ChatMessage(role="system", content=f"{BRAINSTORM}\n\n{data}"),
            *(ChatMessage(role=m.role, content=m.content) for m in body.messages),
        ],
        max_tokens=900,
        temperature=0.6,
    )
    try:
        reply = await llm.complete(request)
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    match = FINAL_RE.search(reply.text)
    final = match.group(1).strip() if match else None
    text = reply.text[: match.start()].strip() if match else reply.text.strip()
    return {"reply": text, "final_prompt": final, "model": reply.model}


async def _project_context(db: DbDep, project: Project) -> str:
    """Fenced transcript + current edit summary (empty before analysis finishes)."""
    row = await db_ops.find_one(db, TimelineRow, {"project_id": project.id})
    if row is None:
        return "<project_data>\n(video not analysed yet: no transcript)\n</project_data>"
    tl = await load_version(db, row.id, await get_current(db, row.id))
    bundle = await latest_bundle(db, get_storage(get_settings()), project.id)
    speech = list(bundle.speech) if bundle else []
    fillers = list(bundle.fillers) if bundle else []
    return project_data(tl, speech, fillers)
