"""Studio API: page, project/render listings, brainstorm (scripted model). Opt-in."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.studio import get_brainstorm_llm
from app.main import create_app
from app.providers.base import ChatReply, ChatRequest

pytestmark = pytest.mark.integration


class _Brain:
    """Scripted brainstorm model: records the request, returns a fixed reply."""

    provider_name = "scripted"

    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatReply:
        self.requests.append(request)
        return ChatReply(text=self.text, model="scripted")


def _client(llm: _Brain, key: str = "studio-key") -> AsyncClient:
    app = create_app()
    app.dependency_overrides[get_brainstorm_llm] = lambda: llm
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": key}
    )


def _words() -> list[dict[str, object]]:
    return [
        {"id": f"w{i + 1}", "start": i * 0.5, "end": i * 0.5 + 0.4, "text": t,
         "display_text": t, "lang": "en"}
        for i, t in enumerate(["I", "use", "Claude", "daily"])
    ]  # fmt: skip


async def test_page_projects_renders_and_brainstorm() -> None:
    brain = _Brain("- Hook: w3 Claude\n- Logo on w3\nFINAL PROMPT: add captions and a Claude logo")
    async with _client(brain) as client:
        page = await client.get("/app")
        assert page.status_code == 200 and "ClipZ Studio" in page.text

        empty = await client.post("/projects", json={"name": "fresh"})
        ready = await client.post(
            "/projects",
            json={
                "name": "ready",
                "transcript": {"words": _words(), "language": "en"},
                "source_asset": "master",
                "source_duration_s": 2.0,
            },
        )
        projects = (await client.get("/projects")).json()["projects"]
        status = {p["name"]: p["status"] for p in projects}
        assert status == {"fresh": "new", "ready": "ready"}
        assert projects[0]["name"] == "ready"  # newest first

        pid = ready.json()["project_id"]
        assert (await client.get(f"/projects/{pid}/renders")).json() == {"renders": []}
        assert (await client.get(f"/projects/{pid}/source")).status_code == 404  # no upload

        resp = await client.post(
            f"/projects/{pid}/brainstorm",
            json={"messages": [{"role": "user", "content": "make it punchy"}]},
        )
        body = resp.json()
        assert body["final_prompt"] == "add captions and a Claude logo"
        assert "FINAL PROMPT" not in body["reply"] and "Hook" in body["reply"]
        system = brain.requests[0].messages[0].content
        assert system.startswith("ROLE") and "[w3] Claude" in system  # transcript is data

        unready = await client.post(
            f"/projects/{empty.json()['project_id']}/brainstorm",
            json={"messages": [{"role": "user", "content": "ideas?"}]},
        )
        assert unready.status_code == 200  # works before analysis, says so in the context
        assert "not analysed yet" in brain.requests[1].messages[0].content


async def test_other_users_cannot_see_projects() -> None:
    brain = _Brain("ok")
    async with _client(brain, "owner") as owner, _client(brain, "stranger") as stranger:
        pid = (await owner.post("/projects", json={"name": "mine"})).json()["project_id"]
        assert (await stranger.get("/projects")).json() == {"projects": []}
        assert (await stranger.get(f"/projects/{pid}/renders")).status_code == 404
        bad = await stranger.post(
            f"/projects/{pid}/brainstorm", json={"messages": [{"role": "user", "content": "x"}]}
        )
        assert bad.status_code == 404


async def test_style_prompt_learns_from_the_users_other_reels() -> None:
    async with _client(_Brain("ok"), "stylist") as client, _client(_Brain("ok"), "other") as other:

        async def project(name: str) -> str:
            body = {
                "name": name,
                "transcript": {"words": _words(), "language": "en"},
                "source_asset": "master",
                "source_duration_s": 2.0,
            }
            return str((await client.post("/projects", json=body)).json()["project_id"])

        target = await project("new reel")
        first = (await client.get(f"/projects/{target}/style-prompt")).json()
        assert first["samples"] == 0 and first["prompt"] is None and "edit a reel" in first["note"]

        captions = {"size_px": 110, "uppercase": True, "position": "center"}
        for name in ("old one", "old two"):
            pid = await project(name)
            op = {"op": "set_captions", "config": captions}
            assert (await client.post(f"/projects/{pid}/ops", json=op)).status_code == 200
        await project("never edited")  # v1 only: nothing kept, not learned from
        # the target's own edits are never learned (they would just be re-applied)
        op = {"op": "set_captions", "config": {"size_px": 60}}
        assert (await client.post(f"/projects/{target}/ops", json=op)).status_code == 200

        body = (await client.get(f"/projects/{target}/style-prompt")).json()
        assert body["samples"] == 2 and body["exported"] == 0
        assert "uppercase" in body["prompt"] and "110 px" in body["prompt"]
        assert {h["area"] for h in body["habits"]} == {"captions"}

        assert (await other.get(f"/projects/{target}/style-prompt")).status_code == 404
        fresh = (await client.post("/projects", json={"name": "uploading"})).json()["project_id"]
        assert (await client.get(f"/projects/{fresh}/style-prompt")).status_code == 409
