"""Ingest worker: upload -> probe -> validate -> normalize -> proxy -> asr audio.

Runs inside arq (or directly in tests). Progress is written to the job document
after every stage so the SSE endpoint can stream it. On success the raw upload
folder (./upload/<project>/) is deleted; derived files live in ./work/<project>/.
"""

import hashlib
import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from app import db as db_ops
from app import telemetry
from app.db import Database
from app.media.errors import IngestError, MediaError
from app.media.ffmpeg import ffmpeg_path, run_capture, run_ffmpeg
from app.media.models import MediaInfo
from app.media.normalize import asr_audio_args, normalize_args, proxy_args, silent_asr_args
from app.media.probe import probe_duration_s, probe_file
from app.media.validate import validate_info
from app.media.waveform import peaks_from_pcm, waveform_json
from app.models import Asset, Job, Project
from app.settings import Settings
from app.storage import LocalStorage, Storage, get_storage

LICENSE_USER_UPLOAD = "user-owned"


async def _set_job(
    db: Database, job_id: uuid.UUID, status: str, progress: float, error: str = ""
) -> None:
    await db_ops.update(
        db, Job, job_id, {"status": status, "progress": progress, "error": error or None}
    )


async def _existing_master(db: Database, project_id: uuid.UUID, source_sha: str) -> Asset | None:
    return await db_ops.find_one(
        db, Asset, {"project_id": project_id, "kind": "master", "source_sha": source_sha}
    )


async def _put_asset(
    db: Database,
    store: Storage | None,
    *,
    project_id: uuid.UUID,
    kind: str,
    storage_key: str,
    data: bytes | None,
    content_type: str,
    sha256: str,
    info: MediaInfo | None = None,
    source_sha: str | None = None,
) -> Asset:
    """Record an asset; write its bytes first unless they are already stored."""
    if store is not None and data is not None:
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
    return await db_ops.insert(db, asset)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


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
    db = db_ops.get_db(settings)
    # Idempotency: master already built from these source bytes?
    if await _existing_master(db, pid, sha256):
        await _set_job(db, jid, "done", 1.0)
        return {"status": "done", "reused": True}
    started = time.monotonic()
    try:
        result = await _run_pipeline(db, store, jid, pid, storage_key, sha256)
        result["analysis_job_id"] = await queue_analysis(ctx, db, pid, sha256)
        media = await _existing_master(db, pid, sha256)
        await telemetry.stage(
            db,
            "ingest",
            _ms(started),
            project_id=pid,
            job_id=jid,
            media_s=(media.duration_ms or 0) / 1000 if media else 0.0,
        )
        return result
    except (MediaError, IngestError, ValueError) as exc:
        code = getattr(exc, "code", "failed")
        await _set_job(db, jid, "failed", 1.0, f"[{code}] {exc}")
        await telemetry.stage(db, "ingest", _ms(started), project_id=pid, job_id=jid, ok=False)
        return {"status": "failed", "error": str(exc)}


async def _run_pipeline(
    db: Database,
    store: Storage,
    jid: uuid.UUID,
    pid: uuid.UUID,
    storage_key: str,
    sha256: str,
) -> dict[str, object]:
    await _set_job(db, jid, "running", 0.05)
    with tempfile.TemporaryDirectory(prefix="clipz-ingest-") as tmp:
        tmpdir = Path(tmp)
        raw_local = store.path_for(storage_key)
        if not raw_local.exists():
            raise IngestError("missing_upload", f"upload {storage_key} not found in storage")
        actual = _sha256_file(raw_local)
        if actual != sha256:
            raise IngestError("sha_mismatch", "upload bytes do not match the declared sha256")
        await _set_job(db, jid, "running", 0.1)

        info = probe_file(str(raw_local))
        validate_info(info)
        await _set_job(db, jid, "running", 0.2)

        await _put_asset(
            db,
            None,  # bytes already in ./upload; the file is removed after ingest
            project_id=pid,
            kind="raw",
            storage_key=storage_key,
            data=None,
            content_type="video/mp4",
            sha256=sha256,
            info=info,
        )

        master_local = tmpdir / "master.mp4"
        run_ffmpeg(normalize_args(str(raw_local), str(master_local), info))
        master_bytes = master_local.read_bytes()
        await _set_job(db, jid, "running", 0.5)
        master_info = probe_file(str(master_local))
        await _put_asset(
            db,
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
        await _set_job(db, jid, "running", 0.8)
        await _put_asset(
            db,
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
            db,
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
            db,
            store,
            project_id=pid,
            kind="waveform",
            storage_key=f"projects/{pid}/waveform.json",
            data=wf_bytes,
            content_type="application/json",
            sha256=hashlib.sha256(wf_bytes).hexdigest(),
            source_sha=sha256,
        )
        await _set_job(db, jid, "running", 0.9)

    store.delete_tree(f"uploads/{pid}")
    await _set_job(db, jid, "done", 1.0)
    return {"status": "done", "reused": False}


async def queue_analysis(
    ctx: dict[str, Any], db: Database, pid: uuid.UUID, sha256: str
) -> str | None:
    """Create + enqueue the analysis job for freshly ingested bytes (idempotent)."""
    project = await db_ops.get(db, Project, pid)
    if project is None:
        return None
    idem = f"analyze:{pid}:{sha256}"
    if await db_ops.find_one(db, Job, {"idempotency_key": idem}) is not None:
        return None
    job = await db_ops.insert(db, Job(project_id=pid, type="analyze", idempotency_key=idem))
    redis = ctx.get("redis")
    if redis is not None:  # absent when tests call the task directly
        await redis.enqueue_job(
            "analyze_asset",
            job_id=str(job.id),
            project_id=str(pid),
            source_sha=sha256,
            language=project.language,
            caption_script=project.caption_script,
        )
    return str(job.id)
