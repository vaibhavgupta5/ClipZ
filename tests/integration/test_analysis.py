"""Analysis pipeline integration test: fake stages, real cache/endpoints. Opt-in."""

import hashlib
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app import db as db_ops
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


async def _job(project_id: str) -> str:
    job = await db_ops.insert(
        db_ops.get_db(),
        Job(
            project_id=uuid.UUID(project_id),
            type="analyze",
            idempotency_key=f"analyze:itest-{uuid.uuid4().hex}",
        ),
    )
    return str(job.id)


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
        import asyncio as _asyncio

        data = await _asyncio.to_thread(Path("fixtures/synth/bars_5s.mp4").read_bytes)
        source_sha = hashlib.sha256(data).hexdigest()
        prefix = f"projects/{project_id}"
        store.put_bytes(f"{prefix}/master.mp4", data, "video/mp4")
        store.put_bytes(f"{prefix}/asr.wav", data, "video/mp4")  # stages use fakes

        job_id = await _job(project_id)

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
        row = await db_ops.get(db_ops.get_db(), Job, uuid.UUID(job_id))
        assert row is not None and row.status == "done" and row.progress == 1.0

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
        job_id2 = await _job(project_id)
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
