"""Ingest worker: download -> probe -> validate -> normalize -> proxy -> asr audio.

Runs inside arq (or directly in tests). Progress is COMMITTED to the jobs row
after every stage so the SSE endpoint can stream it. Temp dir always cleaned.
"""

import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db as db_module
from app.media.errors import IngestError, MediaError
from app.media.ffmpeg import ffmpeg_path, run_capture, run_ffmpeg
from app.media.models import MediaInfo
from app.media.normalize import asr_audio_args, normalize_args, proxy_args, silent_asr_args
from app.media.probe import probe_duration_s, probe_file
from app.media.validate import validate_info
from app.media.waveform import peaks_from_pcm, waveform_json
from app.models import Asset, Job
from app.settings import Settings
from app.storage import Storage, get_storage

LICENSE_USER_UPLOAD = "user-owned"


async def _set_job(
    session: AsyncSession, job_id: uuid.UUID, status: str, progress: float, error: str = ""
) -> None:
    job = await session.get(Job, job_id)
    if job is None:
        raise ValueError(f"job {job_id} does not exist")
    job.status = status
    job.progress = progress
    job.error = error or None
    await session.commit()


async def _existing_master(
    session: AsyncSession, project_id: uuid.UUID, source_sha: str
) -> Asset | None:
    return await session.scalar(
        select(Asset).where(
            Asset.project_id == project_id, Asset.kind == "master", Asset.source_sha == source_sha
        )
    )


async def _put_asset(
    session: AsyncSession,
    store: Storage,
    *,
    project_id: uuid.UUID,
    kind: str,
    storage_key: str,
    data: bytes,
    content_type: str,
    sha256: str,
    info: MediaInfo | None = None,
    source_sha: str | None = None,
) -> Asset:
    store.put_bytes(storage_key, data, content_type)
    asset = Asset(
        project_id=project_id,
        kind=kind,
        storage_key=storage_key,
        sha256=sha256,
        source_sha=source_sha,
        source="user_upload",
        license=LICENSE_USER_UPLOAD,
        attribution_required=False,
        width=info.width if info else None,
        height=info.height if info else None,
        duration_ms=int(info.duration_s * 1000) if info else None,
    )
    session.add(asset)
    await session.flush()
    return asset


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _peaks_for_wav(wav_path: str) -> tuple[list[float], float]:
    """Decode to mono f32 via ffmpeg pipe; return (peaks, duration_s)."""
    duration_s = probe_duration_s(wav_path)
    pcm = run_capture(
        [
            ffmpeg_path(),
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-i",
            wav_path,
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "f32le",
            "-",
        ]
    )
    return (peaks_from_pcm(pcm, 16000), duration_s)


async def ingest_asset(
    ctx: dict[str, Any],
    *,
    job_id: str,
    project_id: str,
    storage_key: str,
    sha256: str,
) -> dict[str, object]:
    """Full ingest pipeline. Returns {'status': 'done'|'failed', ...} (never raises)."""
    settings: Settings = ctx["settings"]
    jid, pid = uuid.UUID(job_id), uuid.UUID(project_id)
    store = get_storage(settings)
    engine = db_module.create_engine(settings)
    try:
        factory = db_module.session_factory(engine)
        async with factory() as session:
            # Idempotency: master already built from these source bytes?
            if await _existing_master(session, pid, sha256):
                await _set_job(session, jid, "done", 1.0)
                return {"status": "done", "reused": True}
            try:
                async with factory() as work:
                    result = await _run_pipeline(work, store, jid, pid, storage_key, sha256)
                return result
            except (MediaError, IngestError, ValueError) as exc:
                async with factory() as work:
                    code = getattr(exc, "code", "failed")
                    await _set_job(work, jid, "failed", 1.0, f"[{code}] {exc}")
                return {"status": "failed", "error": str(exc)}
    finally:
        await engine.dispose()


async def _run_pipeline(
    session: AsyncSession,
    store: Storage,
    jid: uuid.UUID,
    pid: uuid.UUID,
    storage_key: str,
    sha256: str,
) -> dict[str, object]:
    await _set_job(session, jid, "running", 0.05)
    with tempfile.TemporaryDirectory(prefix="reelforge-ingest-") as tmp:
        tmpdir = Path(tmp)
        raw_local = tmpdir / "raw.mp4"
        try:
            raw_local.write_bytes(store.get_bytes(storage_key))
        except KeyError:
            raise IngestError(
                "missing_upload", f"upload {storage_key} not found in storage"
            ) from None
        actual = _sha256_file(raw_local)
        if actual != sha256:
            raise IngestError("sha_mismatch", "upload bytes do not match the declared sha256")
        await _set_job(session, jid, "running", 0.1)

        info = probe_file(str(raw_local))
        validate_info(info)
        await _set_job(session, jid, "running", 0.2)

        await _put_asset(
            session,
            store,
            project_id=pid,
            kind="raw",
            storage_key=storage_key,
            data=raw_local.read_bytes(),
            content_type="video/mp4",
            sha256=sha256,
            info=info,
        )

        master_local = tmpdir / "master.mp4"
        run_ffmpeg(normalize_args(str(raw_local), str(master_local), info))
        master_bytes = master_local.read_bytes()
        await _set_job(session, jid, "running", 0.5)
        master_info = probe_file(str(master_local))
        await _put_asset(
            session,
            store,
            project_id=pid,
            kind="master",
            storage_key=f"projects/{pid}/master.mp4",
            data=master_bytes,
            content_type="video/mp4",
            sha256=hashlib.sha256(master_bytes).hexdigest(),
            info=master_info,
            source_sha=sha256,
        )

        proxy_local = tmpdir / "proxy.mp4"
        run_ffmpeg(proxy_args(str(master_local), str(proxy_local)))
        proxy_bytes = proxy_local.read_bytes()
        await _set_job(session, jid, "running", 0.8)
        await _put_asset(
            session,
            store,
            project_id=pid,
            kind="proxy",
            storage_key=f"projects/{pid}/proxy.mp4",
            data=proxy_bytes,
            content_type="video/mp4",
            sha256=hashlib.sha256(proxy_bytes).hexdigest(),
            info=probe_file(str(proxy_local)),
            source_sha=sha256,
        )

        wav_local = tmpdir / "asr.wav"
        if info.acodec is not None:
            run_ffmpeg(asr_audio_args(str(master_local), str(wav_local)))
        else:
            run_ffmpeg(silent_asr_args(master_info.duration_s, str(wav_local)))
        wav_bytes = wav_local.read_bytes()
        await _put_asset(
            session,
            store,
            project_id=pid,
            kind="asr_audio",
            storage_key=f"projects/{pid}/asr.wav",
            data=wav_bytes,
            content_type="audio/wav",
            sha256=hashlib.sha256(wav_bytes).hexdigest(),
            source_sha=sha256,
        )
        peaks, wav_dur = _peaks_for_wav(str(wav_local))
        wf_doc = waveform_json(peaks, wav_dur)
        wf_bytes = json.dumps(wf_doc).encode()
        await _put_asset(
            session,
            store,
            project_id=pid,
            kind="waveform",
            storage_key=f"projects/{pid}/waveform.json",
            data=wf_bytes,
            content_type="application/json",
            sha256=hashlib.sha256(wf_bytes).hexdigest(),
            source_sha=sha256,
        )
        await _set_job(session, jid, "running", 0.9)
        await session.commit()

    await _set_job(session, jid, "done", 1.0)
    return {"status": "done", "reused": False}
