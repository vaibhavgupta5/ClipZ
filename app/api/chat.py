"""Chat API: POST a message, receive the Director's events as SSE (Phase 5 T5).

Events: token, tool_call, tool_result, timeline_version_created, preview_queued,
render_queued, ask, done, then render_progress / preview_ready / render_ready /
render_failed while this turn's renders run (bounded by CHAT_PREVIEW_WAIT_S), error.
The user message and the assistant reply (with the tool calls made) are persisted,
and every run is logged to agent_runs.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app import db as db_ops
from app.agent.context import project_data, system_prompt
from app.agent.director import Event, run_director
from app.agent.runs import log_run
from app.agent.workspace import DbWorkspace, latest_bundle
from app.api.deps import DbDep, UserDep, owned_project
from app.api.timelines import _require_timeline
from app.logging_setup import get_logger
from app.models import Job, Message, Render
from app.providers.gemini import ProviderUnavailable
from app.providers.registry import director_llm
from app.providers.tools import AgentMessage, ToolCallingLLM
from app.settings import get_settings
from app.storage import get_storage

router = APIRouter()
log = get_logger(component="chat")

HISTORY_TURNS = 10  # prior chat messages (text only) sent with each request


class ChatRequestBody(BaseModel):
    """One user chat message."""

    text: str = Field(min_length=1, max_length=2000, description="What the user wants")


def get_director_llm() -> ToolCallingLLM:
    """Director model dependency (overridden in tests)."""
    return director_llm(get_settings())


LlmDep = Annotated[ToolCallingLLM, Depends(get_director_llm)]


async def _history(db: Any, project_id: uuid.UUID) -> list[AgentMessage]:
    rows = await db_ops.find(
        db, Message, {"project_id": project_id}, sort=[("created_at", -1)], limit=HISTORY_TURNS
    )
    return [
        AgentMessage(role="user" if r.role == "user" else "assistant", text=r.content)
        for r in reversed(rows)
        if r.content
    ]


async def _follow_renders(
    db: Any, render_ids: list[str], queue: "asyncio.Queue[Event | None]"
) -> None:
    """Stream progress of this turn's renders until done/failed or CHAT_PREVIEW_WAIT_S passes."""
    deadline = time.monotonic() + get_settings().CHAT_PREVIEW_WAIT_S
    pending = {uuid.UUID(r) for r in render_ids}
    last: dict[uuid.UUID, float] = {}
    while pending and time.monotonic() < deadline:
        for rid in list(pending):
            render = await db_ops.get(db, Render, rid)
            if render is None:
                pending.discard(rid)
                continue
            if render.status == "done":
                kind: Any = "preview_ready" if render.profile == "preview" else "render_ready"
                queue.put_nowait(Event(kind, {"render_id": str(rid), "url": render.output_url}))
                pending.discard(rid)
            elif render.status == "failed":
                queue.put_nowait(Event("render_failed", {"render_id": str(rid)}))
                pending.discard(rid)
            else:
                job = await db_ops.find_one(db, Job, {"idempotency_key": f"render:{rid}"})
                progress = job.progress if job else 0.0
                if progress != last.get(rid):
                    last[rid] = progress
                    queue.put_nowait(
                        Event("render_progress", {"render_id": str(rid), "progress": progress})
                    )
        if pending:
            await asyncio.sleep(0.5)


def _sse(event: Event) -> dict[str, str]:
    return {"event": event.type, "data": json.dumps(event.data, default=str)}


@router.post("/projects/{project_id}/messages")
async def post_message(
    project_id: uuid.UUID,
    body: ChatRequestBody,
    db: DbDep,
    user_id: UserDep,
    llm: LlmDep,
) -> EventSourceResponse:
    """Run the Director on one message and stream its events."""
    project = await owned_project(db, user_id, project_id)
    row = await _require_timeline(db, project)
    store = get_storage(get_settings())
    bundle = await latest_bundle(db, store, project.id)
    ws = DbWorkspace(db, project.id, row, bundle)
    state = await ws.state()
    speech = bundle.speech if bundle else []
    fillers = bundle.fillers if bundle else []
    system = system_prompt(project_data(state.timeline, list(speech), list(fillers)))
    history = await _history(db, project.id)
    await db_ops.insert(db, Message(project_id=project.id, role="user", content=body.text))

    queue: asyncio.Queue[Event | None] = asyncio.Queue()

    async def run() -> None:
        started = time.monotonic()
        first_token: list[int] = []

        def emit(event: Event) -> None:
            if event.type == "token" and not first_token:
                first_token.append(int((time.monotonic() - started) * 1000))
            queue.put_nowait(event)

        try:
            result = await run_director(llm, ws, system, body.text, history=history, emit=emit)
            await db_ops.insert(
                db,
                Message(
                    project_id=project.id,
                    role="assistant",
                    content=result.reply,
                    tool_calls=[{"name": t} for t in result.tools],
                ),
            )
            await log_run(
                db,
                project.id,
                system,
                result,
                latency_ms=int((time.monotonic() - started) * 1000),
                first_token_ms=first_token[0] if first_token else None,
            )
            await _follow_renders(db, result.renders, queue)
        except ProviderUnavailable as exc:
            queue.put_nowait(Event("error", {"message": str(exc)}))
        except Exception as exc:  # noqa: BLE001 - surface as an event, never a hung stream
            log.exception("director run failed", project_id=str(project.id))
            queue.put_nowait(Event("error", {"message": f"{type(exc).__name__}: {exc}"}))
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(run())

    async def stream() -> AsyncIterator[dict[str, str]]:
        try:
            while (event := await queue.get()) is not None:
                yield _sse(event)
        finally:
            await task

    return EventSourceResponse(stream())


@router.get("/projects/{project_id}/messages")
async def list_messages(project_id: uuid.UUID, db: DbDep, user_id: UserDep) -> dict[str, object]:
    """Chat history, oldest first."""
    await owned_project(db, user_id, project_id)
    rows = await db_ops.find(db, Message, {"project_id": project_id}, sort=[("created_at", 1)])
    return {
        "messages": [
            {
                "role": r.role,
                "content": r.content,
                "tool_calls": r.tool_calls,
                "at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }
