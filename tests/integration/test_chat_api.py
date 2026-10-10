"""Chat API end to end with a scripted Director model (real Mongo + Redis). Opt-in."""

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app import db as db_ops
from app.api.chat import get_director_llm
from app.main import create_app
from app.models import AgentRun, Job, Render
from app.providers.fakes import ScriptedToolLLM
from app.providers.tools import ToolCall, ToolTurnReply, Usage

pytestmark = pytest.mark.integration

API_KEY = "chat-itest-key"


def _words() -> list[dict[str, object]]:
    return [
        {
            "id": f"w{i + 1}",
            "start": i * 0.5,
            "end": i * 0.5 + 0.4,
            "text": t,
            "display_text": t,
            "lang": "en",
        }
        for i, t in enumerate(["so", "today", "we", "ship", "it"])
    ]


def _app(script: list) -> tuple[AsyncClient, ScriptedToolLLM]:  # type: ignore[type-arg]
    llm = ScriptedToolLLM(script, model="gemini-3.8-flash")
    app = create_app()
    app.dependency_overrides[get_director_llm] = lambda: llm
    client = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": API_KEY}
    )
    return client, llm


async def _project(client: AsyncClient) -> str:
    resp = await client.post(
        "/projects",
        json={
            "name": "chat",
            "transcript": {"words": _words(), "language": "en"},
            "source_asset": "master",
            "source_duration_s": 2.5,
        },
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["project_id"])


async def _chat(
    client: AsyncClient, project_id: str, text: str
) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    async with client.stream(
        "POST", f"/projects/{project_id}/messages", json={"text": text}
    ) as resp:
        assert resp.status_code == 200
        name = ""
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line.split(":", 1)[1])))
    return events


async def test_chat_turn_edits_previews_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.settings import get_settings

    monkeypatch.setenv("CHAT_PREVIEW_WAIT_S", "0")  # no worker here; see test_follow_renders
    get_settings.cache_clear()
    script = [
        ToolTurnReply(
            calls=[
                ToolCall(id="a", name="add_captions", args={"highlight_color": "#FFD400"}),
                ToolCall(id="b", name="denoise", args={}),
            ],
            stop_reason="tool_use",
            usage=Usage(input_tokens=1000, output_tokens=50),
        ),
        ToolTurnReply(
            text="Yellow captions on, audio cleaned.",
            usage=Usage(input_tokens=1200, output_tokens=20),
        ),
    ]
    client, llm = _app(script)
    async with client:
        pid = await _project(client)
        events = await _chat(client, pid, "add yellow captions and denoise")
        names = [n for n, _ in events]
        assert names.count("tool_call") == 2 and names.count("timeline_version_created") == 2
        assert "preview_queued" in names and names[-1] == "done"
        assert events[-1][1]["reply"].startswith("Yellow captions on, audio cleaned.")
        assert "Applied: add_captions, denoise." in events[-1][1]["reply"]

        tl = (await client.get(f"/projects/{pid}/timeline")).json()
        assert tl["version"] == 3
        assert tl["timeline"]["captions"]["highlight_color"] == "#FFD400"

        history = (await client.get(f"/projects/{pid}/messages")).json()["messages"]
        assert [m["role"] for m in history] == ["user", "assistant"]

        # project data reached the model fenced in the system prompt
        assert "[w4] ship" in llm.requests[0].system

        db = db_ops.get_db()
        run = await db_ops.find_one(db, AgentRun, {"project_id": uuid.UUID(pid)})
        assert run is not None and run.tools == ["add_captions", "denoise"]
        assert run.input_tokens == 2200 and run.cost_usd > 0
        render = await db_ops.find_one(db, Render, {"project_id": uuid.UUID(pid)})
        assert render is not None and render.profile == "preview"
        job = await db_ops.find_one(db, Job, {"idempotency_key": f"render:{render.id}"})
        assert job is not None and job.type == "render"

        # Tiger Data telemetry: tool calls linked to the run, rollups and project history
        from app.models import ToolCallLog

        calls = await db_ops.find(db, ToolCallLog, {"run_id": run.id})
        assert sorted(c.tool for c in calls) == ["add_captions", "denoise"]
        assert all(c.ok for c in calls)
        overview = (await client.get("/metrics/overview?hours=2")).json()
        assert overview["totals"][0]["runs"] == 1
        assert overview["totals"][0]["cost_usd"] == pytest.approx(run.cost_usd)
        assert {t["tool"] for t in overview["tools"]} == {"add_captions", "denoise"}
        project_runs = (await client.get(f"/projects/{pid}/metrics")).json()["runs"]
        assert [t["tool"] for t in project_runs[0]["tool_calls"]] == ["add_captions", "denoise"]

        # next turn sees the prior conversation; bare "undo" skips the model
        events = await _chat(client, pid, "undo")
        assert events[-1][1]["reply"].startswith("Undo done")
        assert len(llm.requests) == 2  # no new model call
        tl = (await client.get(f"/projects/{pid}/timeline")).json()
        assert tl["version"] == 2


async def test_chat_without_gemini_key_reports_error() -> None:
    from app.providers.gemini import GeminiLLM

    app = create_app()
    app.dependency_overrides[get_director_llm] = lambda: GeminiLLM(api_key="")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": API_KEY}
    ) as client:
        pid = await _project(client)
        events = await _chat(client, pid, "make captions bigger")
        assert events == [("error", {"message": "GEMINI_API_KEY is empty; set it to use Gemini"})]


async def test_follow_renders_streams_progress_then_ready() -> None:
    import asyncio

    from app.agent.director import Event
    from app.api.chat import _follow_renders
    from app.models import Project, User

    db = db_ops.get_db()
    user = await db_ops.insert(db, User(api_key_hash="follow"))
    project = await db_ops.insert(db, Project(user_id=user.id, name="follow"))
    render = await db_ops.insert(
        db, Render(project_id=project.id, timeline_version_id=uuid.uuid4(), profile="preview")
    )
    await db_ops.insert(
        db,
        Job(type="render", idempotency_key=f"render:{render.id}", status="running", progress=0.4),
    )
    queue: asyncio.Queue[Event | None] = asyncio.Queue()

    async def finish_later() -> None:
        await asyncio.sleep(0.7)
        await db_ops.update(
            db, Render, render.id, {"status": "done", "output_url": "/media/renders/x/p.mp4"}
        )

    await asyncio.gather(_follow_renders(db, [str(render.id)], queue), finish_later())
    events = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [e.type for e in events] == ["render_progress", "preview_ready"]  # type: ignore[union-attr]
    assert events[-1].data["url"] == "/media/renders/x/p.mp4"  # type: ignore[union-attr]
