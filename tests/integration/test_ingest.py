"""Ingest pipeline integration tests: real ffmpeg vs compose stack. Opt-in."""

import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.db import create_engine, session_factory
from app.models import Asset, Job, Project, User
from app.models import Timeline as TimelineRow
from app.settings import get_settings
from app.workers.ingest import ingest_asset

pytestmark = pytest.mark.integration

CTX = {"settings": get_settings()}


async def _seed(kind_suffix: str = "") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    engine = create_engine(get_settings())
    try:
        factory = session_factory(engine)
        async with factory() as session:
            user = User(api_key_hash=f"ingest-{kind_suffix}-{uuid.uuid4().hex}")
            session.add(user)
            await session.flush()
            project = Project(user_id=user.id, name="ingest-itest")
            session.add(project)
            await session.flush()
            job = Job(
                project_id=project.id,
                type="ingest",
                status="queued",
                idempotency_key=f"ingest:itest-{uuid.uuid4().hex}",
                payload={},
            )
            session.add(job)
            await session.flush()
            ids = (user.id, project.id, job.id)
            await session.commit()
            return ids
    finally:
        await engine.dispose()


async def _cleanup(user_id: uuid.UUID) -> None:
    engine = create_engine(get_settings())
    try:
        async with engine.begin() as conn:
            pids = select(Project.id).where(Project.user_id == user_id)
            tids = select(TimelineRow.id).where(TimelineRow.project_id.in_(pids))
            from app.models import Message, Render, TimelineVersion

            await conn.execute(delete(Render).where(Render.project_id.in_(pids)))
            await conn.execute(delete(Message).where(Message.project_id.in_(pids)))
            await conn.execute(delete(TimelineVersion).where(TimelineVersion.timeline_id.in_(tids)))
            await conn.execute(delete(TimelineRow).where(TimelineRow.id.in_(tids)))
            await conn.execute(delete(Job).where(Job.project_id.in_(pids)))
            await conn.execute(delete(Asset).where(Asset.project_id.in_(pids)))
            await conn.execute(delete(Project).where(Project.id.in_(pids)))
            await conn.execute(delete(User).where(User.id == user_id))
    finally:
        await engine.dispose()


async def _run_fixture(name: str, data: bytes | None = None) -> dict[str, object]:
    import asyncio

    from app.storage import get_storage

    if data is None:
        data = await asyncio.to_thread(Path(f"fixtures/synth/{name}.mp4").read_bytes)
    raw = data
    sha = hashlib.sha256(raw).hexdigest()
    user_id, project_id, job_id = await _seed(name)
    key = f"itest/{uuid.uuid4().hex}/{name}.mp4"
    get_storage(get_settings()).put_bytes(key, raw, "video/mp4")
    try:
        result = await ingest_asset(
            CTX, job_id=str(job_id), project_id=str(project_id), storage_key=key, sha256=sha
        )
        return {"result": result, "user_id": user_id, "project_id": project_id, "job_id": job_id}
    except Exception:
        await _cleanup(user_id)
        raise


async def test_full_pipeline_bars_5s() -> None:
    import json

    from app.storage import get_storage

    out = await _run_fixture("bars_5s")
    try:
        assert out["result"]["status"] == "done"
        store = get_storage(get_settings())
        master = store.get_bytes(f"projects/{out['project_id']}/master.mp4")
        assert len(master) > 100_000
        wf = json.loads(store.get_bytes(f"projects/{out['project_id']}/waveform.json"))
        assert len(wf["peaks"]) == 50  # 5 s x 10 buckets

        import os
        import tempfile

        from app.media.probe import probe_file

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(master)
            path = f.name
        try:
            info = probe_file(path)
        finally:
            os.unlink(path)
        assert info.is_cfr and abs(info.fps - 30.0) < 0.01
        assert abs(info.duration_s - 5.0) < 0.04
    finally:
        await _cleanup(out["user_id"])  # type: ignore[arg-type]


async def test_91s_rejected_and_91s_job_failed() -> None:
    out = await _run_fixture("bars_91s")
    try:
        assert out["result"]["status"] == "failed"
        assert "too_long" in str(out["result"]["error"])
    finally:
        await _cleanup(out["user_id"])  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["vfr", "rotated", "silent_5s", "mono_5s"])
async def test_fixture_matrix_normalizes(name: str) -> None:
    from app.media.probe import probe_file
    from app.storage import get_storage

    out = await _run_fixture(name)
    try:
        assert out["result"]["status"] == "done", out["result"]
        store = get_storage(get_settings())
        pid = str(out["project_id"])
        master = store.get_bytes(f"projects/{pid}/master.mp4")
        proxy = store.get_bytes(f"projects/{pid}/proxy.mp4")

        import asyncio as _asyncio
        import os
        import tempfile

        async def _probe(data: bytes, suffix: str):  # type: ignore[no-untyped-def]
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(data)
                path = f.name
            try:
                return await _asyncio.to_thread(probe_file, path)
            finally:
                os.unlink(path)

        info = await _probe(master, ".mp4")
        assert info.is_cfr and abs(info.fps - 30.0) < 0.01
        if name == "silent_5s":
            assert info.acodec is None
        else:
            assert info.audio_channels == 2 and info.sample_rate == 48000
        if name == "rotated":
            assert (info.width, info.height) == (1280, 720)  # no metadata to apply (doc'd)

        proxy_info = await _probe(proxy, ".mp4")
        assert proxy_info.width == 540
        # keyframe interval <= 0.6 s on the proxy
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(proxy)
            ppath = f.name
        try:
            import asyncio as _asyncio2
            import json as _json
            import subprocess as _sp

            def _ffprobe_packets() -> list[float]:
                pkts = _sp.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "packet=pts_time,flags",
                        "-of",
                        "json",
                        ppath,
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                return [
                    float(p["pts_time"])
                    for p in _json.loads(pkts.stdout)["packets"]
                    if "K" in p.get("flags", "")
                ]

            def _ffmpeg_seek() -> int:
                seek = _sp.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        "2.5",
                        "-i",
                        ppath,
                        "-frames:v",
                        "1",
                        "-f",
                        "null",
                        "-",
                    ],
                    capture_output=True,
                    check=False,
                )
                return seek.returncode

            times = await _asyncio2.to_thread(_ffprobe_packets)
            gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
            assert gaps and max(gaps) <= 0.6, gaps
            # accurate seek to a 0.5 s boundary
            assert await _asyncio2.to_thread(_ffmpeg_seek) == 0
        finally:
            os.unlink(ppath)
    finally:
        await _cleanup(out["user_id"])  # type: ignore[arg-type]


async def test_corrupt_file_rejected() -> None:
    from app.storage import get_storage

    data = b"this is not a video file" * 100
    sha = hashlib.sha256(data).hexdigest()
    user_id, project_id, job_id = await _seed("corrupt")
    key = f"itest/{uuid.uuid4().hex}/corrupt.mp4"
    get_storage(get_settings()).put_bytes(key, data, "video/mp4")
    try:
        result = await ingest_asset(
            CTX, job_id=str(job_id), project_id=str(project_id), storage_key=key, sha256=sha
        )
        assert result["status"] == "failed"
    finally:
        await _cleanup(user_id)


async def test_ingest_is_idempotent() -> None:
    first = await _run_fixture("bars_30s")
    try:
        assert first["result"]["status"] == "done"
        engine = create_engine(get_settings())
        try:
            factory = session_factory(engine)
            async with factory() as session:
                n1 = len(
                    (
                        await session.scalars(
                            select(Asset).where(Asset.project_id == first["project_id"])
                        )
                    ).all()
                )
        finally:
            await engine.dispose()
        # second run with same bytes, new job, same project
        import asyncio as _asyncio

        from app.storage import get_storage as _store

        data = await _asyncio.to_thread(Path("fixtures/synth/bars_30s.mp4").read_bytes)
        sha = hashlib.sha256(data).hexdigest()
        key2 = f"itest/{uuid.uuid4().hex}/bars_30s.mp4"
        _store(get_settings()).put_bytes(key2, data, "video/mp4")
        engine = create_engine(get_settings())
        try:
            factory = session_factory(engine)
            async with factory() as session:
                job = Job(
                    project_id=first["project_id"],
                    type="ingest",
                    status="queued",
                    idempotency_key=f"ingest:itest-{uuid.uuid4().hex}",
                    payload={},
                )
                session.add(job)
                await session.flush()
                job_id2 = job.id
                await session.commit()
        finally:
            await engine.dispose()
        result2 = await ingest_asset(
            CTX,
            job_id=str(job_id2),
            project_id=str(first["project_id"]),
            storage_key=key2,
            sha256=sha,
        )
        assert result2["status"] == "done"
        assert result2["reused"] is True
        engine = create_engine(get_settings())
        try:
            factory = session_factory(engine)
            async with factory() as session:
                n2 = len(
                    (
                        await session.scalars(
                            select(Asset).where(Asset.project_id == first["project_id"])
                        )
                    ).all()
                )
        finally:
            await engine.dispose()
        assert n1 == n2
    finally:
        await _cleanup(first["user_id"])  # type: ignore[arg-type]


async def test_no_temp_dirs_leak() -> None:
    import asyncio as _asyncio
    import glob

    pattern = "/tmp/reelforge-ingest-*"  # noqa: S108 — asserting worker temp cleanup
    before = set(await _asyncio.to_thread(glob.glob, pattern))
    out = await _run_fixture("bars_5s")
    try:
        assert out["result"]["status"] == "done"
    finally:
        await _cleanup(out["user_id"])  # type: ignore[arg-type]
    assert set(await _asyncio.to_thread(glob.glob, pattern)) == before
