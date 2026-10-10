"""Dub worker: master speech -> ElevenLabs Dubbing -> new timeline version with ``dub`` set.

The dub keeps the master's timing, so every cut, insert and effect of the edit applies
to it unchanged. The dubbed track is cached by (master digest, language) in the
generated-audio library, so re-dubbing the same footage is free (rule 5).
"""

import asyncio
import hashlib
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from app import db as db_ops
from app import telemetry
from app.db import Database
from app.media.ffmpeg import ffmpeg_path, run_ffmpeg
from app.models import Asset, Job
from app.providers.elevenlabs import ElevenLabsClient, ElevenLabsError
from app.providers.generated import GeneratedAudioStore, dub_asset_id
from app.settings import Settings
from app.storage import get_storage
from app.timeline.ops import set_dub
from app.timeline.schema import DubTrack
from app.timeline.versions import get_current, load_version, save_version
from app.workers.queue import enqueue_job

POLL_S = 10.0
MIN_WAIT_S = 300.0


async def request_dub(
    db: Database, redis_url: str, project_id: uuid.UUID, timeline_id: uuid.UUID, language: str
) -> Job:
    """Create the Job row and enqueue ``dub_task``."""
    job = await db_ops.insert(
        db,
        Job(
            project_id=project_id,
            type="dub",
            idempotency_key=f"dub:{timeline_id}:{language}:{uuid.uuid4().hex[:8]}",
            payload={"timeline_id": str(timeline_id), "language": language},
        ),
    )
    await enqueue_job(
        redis_url,
        "dub_task",
        {
            "job_id": str(job.id),
            "project_id": str(project_id),
            "timeline_id": str(timeline_id),
            "language": language,
        },
    )
    return job


async def dub_task(
    ctx: dict[str, Any], *, job_id: str, project_id: str, timeline_id: str, language: str
) -> dict[str, object]:
    """Dub the project's speech and commit the dub onto the current version."""
    settings: Settings = ctx["settings"]
    db = db_ops.get_db(settings)
    jid, pid, tid = uuid.UUID(job_id), uuid.UUID(project_id), uuid.UUID(timeline_id)
    started = time.monotonic()
    await db_ops.update(db, Job, jid, {"status": "running", "progress": 0.05})
    client = None
    try:
        if not settings.ELEVENLABS_API_KEY:
            raise ElevenLabsError("auth", "ELEVENLABS_API_KEY is not set")
        client = ElevenLabsClient(settings.ELEVENLABS_API_KEY)
        store = GeneratedAudioStore(
            settings.ASSETS_DIR, client, settings.elevenlabs_plan, settings.ELEVENLABS_VOICE_ID
        )
        version = await _dub(db, settings, store, client, jid, pid, tid, language)
        await db_ops.update(
            db, Job, jid, {"status": "done", "progress": 1.0, "payload": {"version": version}}
        )
        ms = int((time.monotonic() - started) * 1000)
        await telemetry.stage(db, "dub", ms, project_id=pid, job_id=jid)
        return {"status": "done", "version": version}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        await db_ops.update(db, Job, jid, {"status": "failed", "progress": 1.0, "error": error})
        await telemetry.stage(db, "dub", 0, project_id=pid, job_id=jid, ok=False)
        return {"status": "failed", "error": str(exc)}
    finally:
        if client is not None:
            await client.aclose()


async def _dub(
    db: Database,
    settings: Settings,
    store: GeneratedAudioStore,
    client: ElevenLabsClient,
    jid: uuid.UUID,
    pid: uuid.UUID,
    tid: uuid.UUID,
    language: str,
) -> int:
    master = await db_ops.find_one(db, Asset, {"project_id": pid, "kind": "master"})
    if master is None:
        raise ValueError("no master asset (ingest incomplete or project already exported)")
    master_path = get_storage(settings).path_for(master.storage_key)
    tl = await load_version(db, tid, await get_current(db, tid))
    source = tl.transcript.language if tl.transcript.language in ("en", "hi") else "auto"
    with tempfile.TemporaryDirectory(prefix="reelforge-dub-") as tmp:
        speech = Path(tmp) / "speech.mp3"
        await asyncio.to_thread(_extract_speech, str(master_path), str(speech))
        audio = speech.read_bytes()
    asset_id = dub_asset_id(hashlib.sha256(audio).hexdigest(), language)
    item = store.get(asset_id)
    if item is None:
        job = await client.start_dub(audio, "speech.mp3", source, language)
        await db_ops.update(db, Job, jid, {"progress": 0.2})
        deadline = time.monotonic() + max(MIN_WAIT_S, job.expected_s * 4)
        while (status := await client.dub_status(job.dubbing_id)) != "dubbed":
            if status == "failed":
                raise ElevenLabsError("failed", "ElevenLabs reported the dub failed")
            if time.monotonic() > deadline:
                raise TimeoutError("dubbing did not finish in time")
            await asyncio.sleep(POLL_S)
        data = await client.dubbed_audio(job.dubbing_id, language)
        item = store.add(asset_id, data, f"dub {language}", "dub", 0.0)
    # Apply to the version that is current NOW, so edits made meanwhile are kept.
    current = await get_current(db, tid)
    tl = await load_version(db, tid, current)
    dub = DubTrack(
        language=language,  # type: ignore[arg-type]
        asset=item.id,
        license=item.license,
        attribution_required=item.attribution_required,
        attribution_text=item.attribution_text,
        commercial_use=item.commercial_use,
    )
    new_tl, _ = set_dub(tl, dub)
    version, _ = await save_version(db, tid, new_tl, parent=current)
    return version


def _extract_speech(master: str, out: str) -> None:
    """Master audio as mono MP3 (what Dubbing needs; much smaller than the video)."""
    run_ffmpeg(
        [ffmpeg_path(), "-y", "-i", master, "-vn", "-ac", "1", "-ar", "44100", "-b:a", "128k", out]
    )
