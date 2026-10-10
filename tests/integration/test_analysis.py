"""Analysis pipeline integration test: fake stages, real cache/endpoints. Opt-in."""

import hashlib
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.analysis.fakes import (
    CountingFakeTranscriber,
    FakeBeatDetector,
    FakeEmphasis,
    FakeEnergy,
    FakeFaces,
    FakeFillers,
    FakeScenes,
    FakeVad,
)
from app.db import create_engine, session_factory
from app.main import create_app
from app.models import Job
from app.settings import get_settings
from app.storage import get_storage
from app.workers.analyze import analyze_asset, run_stages

pytestmark = pytest.mark.integration

API_KEY = "phase3-analysis-key"


def _client() -> AsyncClient:
    transport = ASGITransport(app=create_app())
    return AsyncClient(transport=transport, base_url="http://test", headers={"X-API-Key": API_KEY})


def _stages() -> dict:
    return {
        "transcriber": CountingFakeTranscriber(),
        "vad": FakeVad(),
        "scenes": FakeScenes(),
        "energy": FakeEnergy(),
        "fillers": FakeFillers(),
        "emphasis": FakeEmphasis(),
        "beats": FakeBeatDetector(),
        "faces": FakeFaces(),
    }


async def _cleanup(project_id: str) -> None:
    from tests.integration.cleanup import cleanup_user

    _ = project_id
    await cleanup_user(API_KEY)


async def test_run_stages_builds_valid_bundle() -> None:
    stages = _stages()
    bundle = await run_stages(
        "fixtures/synth/bars_5s.mp4", "fixtures/synth/bars_5s.mp4", "en", stages
    )
    assert bundle.language == "en"
    assert [w.id for w in bundle.transcript.words] == ["w1", "w2"]
    assert stages["transcriber"].calls == 1


async def test_full_analysis_flow_with_cache_and_endpoints() -> None:
    store = get_storage(get_settings())
    async with _client() as client:
        created = await client.post("/projects", json={"name": "analysis-flow"})
        assert created.status_code == 200, created.text
        project_id = created.json()["project_id"]

        # timeline not ready before analysis
        pending = await client.get(f"/projects/{project_id}/timeline")
        assert pending.status_code == 404
        try:
            import asyncio as _asyncio

            data = await _asyncio.to_thread(Path("fixtures/synth/bars_5s.mp4").read_bytes)
            source_sha = hashlib.sha256(data).hexdigest()
            prefix = f"analysis-itest/{source_sha}"
            store.put_bytes(f"{prefix}/master.mp4", data, "video/mp4")
            store.put_bytes(f"{prefix}/asr.wav", data, "video/mp4")  # stages use fakes

            engine = create_engine(get_settings())
            try:
                factory = session_factory(engine)
                async with factory() as session:
                    job = Job(
                        project_id=uuid.UUID(project_id),
                        type="analysis",
                        status="queued",
                        idempotency_key=f"analysis:itest-{uuid.uuid4().hex}",
                        payload={},
                    )
                    session.add(job)
                    await session.flush()
                    job_id = str(job.id)
                    await session.commit()
            finally:
                await engine.dispose()

            stages = _stages()
            r1 = await analyze_asset(
                {"settings": get_settings()},
                job_id=job_id,
                project_id=project_id,
                source_sha=source_sha,
                prefix=prefix,
                stages=stages,
            )
            assert r1["status"] == "done", r1
            assert stages["transcriber"].calls == 1

            # job row reached terminal progress
            engine = create_engine(get_settings())
            try:
                factory = session_factory(engine)
                async with factory() as session:
                    row = await session.get(Job, uuid.UUID(job_id))
                    assert row is not None and row.status == "done" and row.progress == 1.0
            finally:
                await engine.dispose()

            # endpoints serve bundle + LLM transcript; timeline v1 auto-created
            bundle_resp = await client.get(f"/projects/{project_id}/analysis")
            assert bundle_resp.status_code == 200
            assert bundle_resp.json()["bundle"]["language"] == "en"
            text_resp = await client.get(f"/projects/{project_id}/transcript")
            assert text_resp.status_code == 200
            assert "[w1] hello" in text_resp.json()["transcript"]
            tl_resp = await client.get(f"/projects/{project_id}/timeline")
            assert tl_resp.status_code == 200
            assert tl_resp.json()["version"] == 1

            # second run: full cache hit, zero model calls
            stages2 = _stages()
            engine = create_engine(get_settings())
            try:
                factory = session_factory(engine)
                async with factory() as session:
                    job2 = Job(
                        project_id=uuid.UUID(project_id),
                        type="analysis",
                        status="queued",
                        idempotency_key=f"analysis:itest-{uuid.uuid4().hex}",
                        payload={},
                    )
                    session.add(job2)
                    await session.flush()
                    job_id2 = str(job2.id)
                    await session.commit()
            finally:
                await engine.dispose()
            r2 = await analyze_asset(
                {"settings": get_settings()},
                job_id=job_id2,
                project_id=project_id,
                source_sha=source_sha,
                prefix=prefix,
                stages=stages2,
            )
            assert r2["status"] == "done" and r2["reused"] is True
            assert stages2["transcriber"].calls == 0
        finally:
            await _cleanup(project_id)
