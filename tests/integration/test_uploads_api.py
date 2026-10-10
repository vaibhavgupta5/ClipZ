"""Upload API + progress SSE integration tests against the compose stack. Opt-in."""

import hashlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app import db as db_ops
from app.main import create_app
from app.models import Job, Project, User
from app.settings import get_settings

pytestmark = pytest.mark.integration


def _client(api_key: str = "phase2-uploads-key") -> AsyncClient:
    transport = ASGITransport(app=create_app())
    return AsyncClient(transport=transport, base_url="http://test", headers={"X-API-Key": api_key})


async def _make_project(client: AsyncClient) -> str:
    resp = await client.post(
        "/projects",
        json={
            "name": "uploads",
            "transcript": {
                "words": [
                    {
                        "id": "w1",
                        "start": 0.0,
                        "end": 0.4,
                        "text": "a",
                        "display_text": "a",
                        "lang": "en",
                    }
                ],
                "language": "en",
            },
            "source_asset": "a1",
            "source_duration_s": 0.4,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["project_id"]


async def test_multipart_upload_starts_ingest_job(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    async with _client() as client:
        project_id = await _make_project(client)
        url = f"/projects/{project_id}/uploads"
        data = b"x" * 4096
        got = await client.post(url, files={"file": ("reel.mp4", data, "video/mp4")})
        assert got.status_code == 200, got.text
        assert got.json()["sha256"] == hashlib.sha256(data).hexdigest()
        assert got.json()["reused"] is False
        upload_root = Path(get_settings().UPLOAD_DIR) / project_id
        assert [p.name for p in upload_root.rglob("*.mp4")] == ["reel.mp4"]

        again = await client.post(url, files={"file": ("reel.mp4", data, "video/mp4")})
        assert again.json() == {**got.json(), "reused": True}
        assert len(list(upload_root.rglob("*.mp4"))) == 1  # duplicate bytes not kept

        bad_name = await client.post(url, files={"file": ("../../etc/passwd", data, "video/mp4")})
        assert bad_name.status_code == 422
        not_video = await client.post(url, files={"file": ("a.txt", data, "text/plain")})
        assert not_video.status_code == 422
        too_big = await client.post(
            url, files={"file": ("big.mp4", b"y" * (1024 * 1024 + 1), "video/mp4")}
        )
        assert too_big.status_code == 413
        assert not list(upload_root.rglob("big.mp4"))  # partial file removed

    async with _client("someone-else") as other:
        foreign = await other.post(url, files={"file": ("reel.mp4", data, "video/mp4")})
        assert foreign.status_code == 404


async def test_job_events_stream_reaches_done() -> None:
    api_key = "sse-key"
    db = db_ops.get_db()
    user = await db_ops.insert(db, User(api_key_hash=hashlib.sha256(api_key.encode()).hexdigest()))
    project = await db_ops.insert(db, Project(user_id=user.id, name="sse"))
    job = await db_ops.insert(
        db,
        Job(
            project_id=project.id,
            type="ingest",
            status="done",
            progress=1.0,
            idempotency_key=f"ingest:sse-{uuid.uuid4().hex}",
        ),
    )
    async with _client(api_key) as client:
        events = f"/projects/{project.id}/jobs/{job.id}/events"
        async with client.stream("GET", events) as resp:
            assert resp.status_code == 200
            body = ""
            async for chunk in resp.aiter_text():
                body += chunk
                if "done" in body:
                    break
            assert '"status": "done"' in body or '"status":"done"' in body
