"""Timeline CRUD API integration tests against the compose stack. Opt-in."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app

pytestmark = pytest.mark.integration

API_KEY = "phase1-itest-key"

TRANSCRIPT = {
    "words": [
        {"id": "w1", "start": 0.0, "end": 0.4, "text": "a", "display_text": "a", "lang": "en"},
        {"id": "w2", "start": 0.5, "end": 0.9, "text": "b", "display_text": "b", "lang": "en"},
        {"id": "w3", "start": 1.0, "end": 1.4, "text": "c", "display_text": "c", "lang": "en"},
    ],
    "language": "en",
}


async def _client() -> AsyncClient:
    transport = ASGITransport(app=create_app())
    return AsyncClient(transport=transport, base_url="http://test", headers={"X-API-Key": API_KEY})


async def test_project_lifecycle_ops_undo_redo() -> None:
    project_id = ""
    # 401 without key (keyless client)
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as naked:
        resp = await naked.post("/projects", json={})
        assert resp.status_code == 401

    async with await _client() as client:
        create = await client.post(
            "/projects",
            json={
                "name": "phase1",
                "transcript": TRANSCRIPT,
                "source_asset": "a1",
                "source_duration_s": 1.4,
            },
        )
        assert create.status_code == 200, create.text
        project_id = create.json()["project_id"]

        tl = await client.get(f"/projects/{project_id}/timeline")
        assert tl.status_code == 200
        assert tl.json()["version"] == 1
        assert len(tl.json()["timeline"]["video_clips"]) == 1

        # delete w2 -> v2
        op = await client.post(
            f"/projects/{project_id}/ops", json={"op": "delete_words", "word_ids": ["w2"]}
        )
        assert op.status_code == 200, op.text
        assert op.json()["version"] == 2
        assert len(op.json()["timeline"]["video_clips"]) == 2

        # bad op -> 422 with actionable body
        bad = await client.post(
            f"/projects/{project_id}/ops", json={"op": "delete_words", "word_ids": ["w99"]}
        )
        assert bad.status_code == 422
        assert "w99" in bad.text

        versions = await client.get(f"/projects/{project_id}/versions")
        assert [v["version"] for v in versions.json()["versions"]] == [1, 2]

        undo = await client.post(f"/projects/{project_id}/undo")
        assert undo.json()["version"] == 1
        assert len(undo.json()["timeline"]["video_clips"]) == 1

        redo = await client.post(f"/projects/{project_id}/redo")
        assert redo.json()["version"] == 2
