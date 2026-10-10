"""Analysis worker: cached AnalysisBundle + timeline bootstrap.

Independent stages run concurrently; beats run after the transcript exists.
Without LLM keys, beats/romanization are skipped (documented degradation).
"""

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import Any

from app import db as db_ops
from app.analysis.beats import detect_beats
from app.analysis.cache import load_cached, save_bundle
from app.analysis.emphasis import tag_emphasis
from app.analysis.energy import EnergyAnalyzer
from app.analysis.fakes import FakeFaces
from app.analysis.fillers import FillerFinder
from app.analysis.models import AnalysisBundle
from app.analysis.romanize import romanize
from app.analysis.scenes import SceneDetector
from app.analysis.vad import EnergyVad
from app.db import Database
from app.media.probe import probe_duration_s
from app.models import Job
from app.models import Timeline as TimelineRow
from app.providers.base import LLMProvider
from app.settings import Settings
from app.storage import Storage, get_storage
from app.timeline.schema import Timeline, VideoClip
from app.timeline.versions import save_version

ANALYZER = "full"
VERSION = "1"


def default_stages(settings: Settings | None = None) -> dict[str, Any]:
    """Production stages: real signals, real ASR (faster-whisper, WHISPER_MODEL)."""
    from app.analysis.asr_whisper import FasterWhisperTranscriber
    from app.settings import get_settings

    return {
        "transcriber": FasterWhisperTranscriber((settings or get_settings()).WHISPER_MODEL),
        "vad": EnergyVad(),
        "scenes": SceneDetector(),
        "energy": EnergyAnalyzer(),
        "fillers": None,  # language known only after transcription
        "emphasis": None,  # uses real tag_emphasis directly
        "beats": None,  # uses real detect_beats directly
        "faces": FakeFaces(),  # MediaPipe wired in GPU worker (Phase 9)
    }


def _default_llm(settings: Settings) -> LLMProvider | None:
    """LLM when keys are configured, else None (beats skipped)."""
    if not settings.GEMINI_API_KEY:
        return None
    from app.providers.registry import provider_registry

    return provider_registry(settings).llm


async def run_stages(
    video_path: str,
    wav_path: str,
    language: str,
    stages: dict[str, Any],
    *,
    caption_script: str = "devanagari",
    llm: LLMProvider | None = None,
) -> AnalysisBundle:
    """Run all stages (independent ones concurrently) and assemble the bundle."""
    transcriber = stages["transcriber"]
    vad = stages["vad"]
    scenes = stages["scenes"]
    energy_an = stages["energy"]
    faces = stages["faces"]

    transcript, speech, cuts, energy, face_boxes = await asyncio.gather(
        transcriber.transcribe(wav_path, language),
        asyncio.to_thread(vad.detect, wav_path),
        asyncio.to_thread(scenes.cuts, video_path),
        asyncio.to_thread(energy_an.curve, wav_path),
        asyncio.to_thread(faces.track, video_path),
    )
    lang = transcript.language if transcript.language != "auto" else language
    fillers_stage = stages.get("fillers")
    if fillers_stage is None:
        fillers_stage = FillerFinder(lang if lang in ("en", "hi") else "en")
    fillers = fillers_stage.find(transcript)

    beats_stage = stages.get("beats")
    emphasis_stage = stages.get("emphasis")
    if beats_stage is not None and emphasis_stage is not None:
        beat_list, emphasis = await asyncio.gather(
            beats_stage.detect(transcript),
            emphasis_stage.tag(transcript, energy),
        )
    elif llm is not None:
        emphasis, beat_list = await asyncio.gather(
            tag_emphasis(transcript, energy, llm),
            detect_beats(transcript, llm),
        )
        if caption_script == "roman":
            transcript = await romanize(transcript, llm)
    else:
        emphasis, beat_list = [], []

    return AnalysisBundle(
        transcript=transcript,
        speech=speech,
        scenes=cuts,
        energy=energy,
        fillers=fillers,
        emphasis=emphasis,
        beats=beat_list,
        faces=face_boxes,
        language=lang,
        analyzer_versions={
            "transcriber": getattr(transcriber, "VERSION", "unknown"),
            "vad": getattr(vad, "VERSION", "unknown"),
            "scenes": getattr(scenes, "VERSION", "unknown"),
            "energy": getattr(energy_an, "VERSION", "unknown"),
            "fillers": FillerFinder.VERSION,
        },
    )


async def _set_job(
    db: Database, job_id: uuid.UUID, status: str, progress: float, error: str = ""
) -> None:
    await db_ops.update(
        db, Job, job_id, {"status": status, "progress": progress, "error": error or None}
    )


async def _ensure_timeline(
    db: Database, project_id: uuid.UUID, bundle: AnalysisBundle, duration_s: float
) -> None:
    """Create timeline v1 from the bundle when the project has none yet."""
    if await db_ops.find_one(db, TimelineRow, {"project_id": project_id}) is not None:
        return
    tl = Timeline(
        project_id=project_id,
        transcript=bundle.transcript,
        video_clips=[VideoClip(id="c1", asset="master", src_in=0.0, src_out=duration_s)],
    )
    row = await db_ops.insert(db, TimelineRow(project_id=project_id, current_version=0))
    await save_version(db, row.id, tl, parent=None)


async def analyze_asset(
    ctx: dict[str, Any],
    *,
    job_id: str,
    project_id: str,
    source_sha: str,
    language: str = "auto",
    caption_script: str = "devanagari",
    prefix: str | None = None,
    stages: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Analyze master+asr assets into a cached bundle. Returns status dict (never raises)."""
    settings: Settings = ctx["settings"]
    jid, pid = uuid.UUID(job_id), uuid.UUID(project_id)
    store = get_storage(settings)
    base = prefix or f"projects/{pid}"
    params: dict[str, object] = {
        "language": language,
        "caption_script": caption_script,
        "asr_model": settings.WHISPER_MODEL,
    }
    db = db_ops.get_db(settings)
    try:
        hit = await load_cached(db, store, pid, source_sha, ANALYZER, VERSION, params)
        if hit is not None:
            # Same bytes analysed before (maybe another project): give THIS project a timeline.
            master = store.path_for(f"{base}/master.mp4")
            duration_s = await asyncio.to_thread(probe_duration_s, str(master))
            await _ensure_timeline(db, pid, hit, duration_s)
            await _set_job(db, jid, "done", 1.0)
            return {"status": "done", "reused": True}
        return await _run_analysis(
            db,
            store,
            jid,
            pid,
            source_sha,
            base,
            language,
            caption_script,
            params,
            stages or default_stages(settings),
            _default_llm(settings),
        )
    except Exception as exc:
        await _set_job(db, jid, "failed", 1.0, f"{type(exc).__name__}: {exc}")
        return {"status": "failed", "error": str(exc)}


async def _run_analysis(
    db: Database,
    store: Storage,
    jid: uuid.UUID,
    pid: uuid.UUID,
    source_sha: str,
    base: str,
    language: str,
    caption_script: str,
    params: dict[str, object],
    stages: dict[str, Any],
    llm: LLMProvider | None,
) -> dict[str, object]:
    await _set_job(db, jid, "running", 0.1)
    with tempfile.TemporaryDirectory(prefix="reelforge-analysis-") as tmp:
        master_local = Path(tmp) / "master.mp4"
        wav_local = Path(tmp) / "asr.wav"
        try:
            master_local.write_bytes(store.get_bytes(f"{base}/master.mp4"))
            wav_local.write_bytes(store.get_bytes(f"{base}/asr.wav"))
        except KeyError as exc:
            raise ValueError(f"analysis input missing: {exc}") from None
        duration_s = await asyncio.to_thread(probe_duration_s, str(master_local))
        await _set_job(db, jid, "running", 0.2)

        bundle = await run_stages(
            str(master_local),
            str(wav_local),
            language,
            stages,
            caption_script=caption_script,
            llm=llm,
        )
        await _set_job(db, jid, "running", 0.8)

        await save_bundle(db, store, pid, source_sha, ANALYZER, VERSION, params, bundle)
        await _ensure_timeline(db, pid, bundle, duration_s)
    await _set_job(db, jid, "done", 1.0)
    return {"status": "done", "reused": False}
