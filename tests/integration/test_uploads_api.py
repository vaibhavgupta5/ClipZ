"""Upload API + progress SSE integration tests against the compose stack. Opt-in."""

import hashlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import create_engine, session_factory
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


async def _cleanup(project_id: str, api_key: str) -> None:
    from tests.integration.cleanup import cleanup_user

    _ = project_id
    await cleanup_user(api_key)


async def test_upload_complete_starts_ingest_job() -> None:
    from app.storage import get_storage

    api_key = "phase2-uploads-key"
    async with _client(api_key) as client:
        project_id = await _make_project(client)
        try:
            got = await client.post(
                f"/projects/{project_id}/uploads",
                json={"filename": "reel.mp4", "content_type": "video/mp4", "size_bytes": 12345},
            )
            assert got.status_code == 200, got.text
            key = got.json()["key"]
            assert key.startswith(f"uploads/{project_id}/")
            assert got.json()["upload_url"].startswith("http")

            bad_name = await client.post(
                f"/projects/{project_id}/uploads",
                json={
                    "filename": "../../etc/passwd",
                    "content_type": "video/mp4",
                    "size_bytes": 1,
                },
            )
            assert bad_name.status_code == 422

            get_storage(get_settings()).put_bytes(key, b"x" * 64, "video/mp4")
            done = await client.post(
                f"/projects/{project_id}/uploads/complete",
                json={"key": key, "sha256": "00" * 32},
            )
            assert done.status_code == 200, done.text
            assert done.json()["job_id"]

            foreign = await client.post(
                f"/projects/{project_id}/uploads/complete",
                json={"key": "uploads/other/x.mp4", "sha256": "00" * 32},
            )
            assert foreign.status_code == 422
        finally:
            await _cleanup(project_id, api_key)


async def test_job_events_stream_reaches_done() -> None:
    api_key = "sse-key"
    engine = create_engine(get_settings())
    project_id = ""
    try:
        factory = session_factory(engine)
        async with factory() as session:
            user = User(api_key_hash=hashlib.sha256(api_key.encode()).hexdigest())
            session.add(user)
            await session.flush()
            project = Project(user_id=user.id, name="sse")
            session.add(project)
            await session.flush()
            project_id = str(project.id)
            job = Job(
                project_id=project.id,
                type="ingest",
                status="done",
                progress=1.0,
                idempotency_key=f"ingest:sse-{uuid.uuid4().hex}",
                payload={},
            )
            session.add(job)
            await session.flush()
            job_id = str(job.id)
            await session.commit()

        async with _client(api_key) as client:
            async with client.stream("GET", f"/projects/{project_id}/jobs/{job_id}/events") as resp:
                assert resp.status_code == 200
                body = ""
                async for chunk in resp.aiter_text():
                    body += chunk
                    if "done" in body:
                        break
                assert '"status": "done"' in body or '"status":"done"' in body
    finally:
        await engine.dispose()
        if project_id:
            await _cleanup(project_id, api_key)
