"""Render worker: Timeline version -> preview/final mp4 under editedVideo/<project>/.

A final export ends the project's working set: ./work/<project>/ is deleted
afterwards (ADR-010). Previews never delete anything.
"""

import asyncio
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app import db as db_ops
from app import telemetry
from app.db import Database
from app.models import Asset, Job, TimelineVersion
from app.models import Render as RenderRow
from app.providers.local import resolve_asset_file
from app.render.errors import RenderError
from app.render.render import profile_for, render_timeline
from app.settings import Settings
from app.storage import LocalStorage, get_storage
from app.timeline.schema import Timeline as TimelineModel

PROGRESS_STEP = 0.1


async def _set_job(
    db: Database, job_id: uuid.UUID, status: str, progress: float, error: str = ""
) -> None:
    await db_ops.update(
        db, Job, job_id, {"status": status, "progress": progress, "error": error or None}
    )


def _progress_writer(db: Database, jid: uuid.UUID, render: RenderRow) -> Callable[[float], None]:
    """Thread-safe progress callback: updates the job row and logs a render_progress tick.

    ffmpeg progress arrives on a worker thread; writes are scheduled onto the event loop
    without waiting, so a slow database never stalls the render.
    """
    loop = asyncio.get_running_loop()

    async def _write(frac: float) -> None:
        await db_ops.update(db, Job, jid, {"status": "running", "progress": frac})
        await telemetry.render_tick(db, render.id, render.project_id, render.profile, frac)

    def push(frac: float) -> None:
        loop.call_soon_threadsafe(lambda: loop.create_task(_write(frac)))

    return push


def attributions(tl: TimelineModel) -> list[str]:
    """Credit lines the post caption must carry (CC BY assets), deduplicated in order."""
    lines = [o.attribution_text for o in tl.overlays if o.attribution_required]
    if tl.music is not None and tl.music.attribution_required:
        lines.append(tl.music.attribution_text)
    lines += [s.attribution_text for s in tl.sfx if s.attribution_text]
    lines += [v.attribution_text for v in tl.voiceovers if v.attribution_required]
    if tl.dub is not None and tl.dub.attribution_required:
        lines.append(tl.dub.attribution_text)
    return list(dict.fromkeys(line for line in lines if line))


async def render_task(ctx: dict[str, Any], *, job_id: str, render_id: str) -> dict[str, object]:
    """Render the timeline snapshot linked to a Render document. Returns a status dict."""
    settings: Settings = ctx["settings"]
    jid, rid = uuid.UUID(job_id), uuid.UUID(render_id)
    db = db_ops.get_db(settings)
    render = await db_ops.get(db, RenderRow, rid)
    if render is None:
        raise ValueError(f"render {rid} does not exist")
    started = time.monotonic()
    stage_name = f"render-{render.profile}"
    try:
        result = await _run_render(db, get_storage(settings), settings, jid, render)
        await telemetry.stage(
            db,
            stage_name,
            int((time.monotonic() - started) * 1000),
            project_id=render.project_id,
            job_id=jid,
            media_s=float(str(result.get("media_s", 0.0))),
        )
        return result
    except Exception as exc:
        await _set_job(db, jid, "failed", 1.0, f"{type(exc).__name__}: {exc}")
        await db_ops.update(db, RenderRow, rid, {"status": "failed"})
        await telemetry.stage(db, stage_name, 0, project_id=render.project_id, job_id=jid, ok=False)
        return {"status": "failed", "error": str(exc)}


async def _run_render(
    db: Database, store: LocalStorage, settings: Settings, jid: uuid.UUID, render: RenderRow
) -> dict[str, object]:
    version_row = await db_ops.get(db, TimelineVersion, render.timeline_version_id)
    if version_row is None:
        raise ValueError("timeline version gone")
    timeline = TimelineModel.model_validate(version_row.edl)
    profile = profile_for(render.profile)
    master = await db_ops.find_one(db, Asset, {"project_id": render.project_id, "kind": "master"})
    if master is None:
        raise ValueError("no master asset (ingest incomplete or project already exported)")
    master_path = store.path_for(master.storage_key)
    if not master_path.exists():
        raise ValueError("master file missing from work dir (project already exported?)")

    asset_files: dict[str, str] = {}
    meme_dir = Path(settings.WORK_DIR) / "cache" / "memes"
    for overlay in timeline.overlays:
        library_file = resolve_asset_file(settings.ASSETS_DIR, meme_dir, overlay.asset)
        if library_file is not None:
            asset_files[overlay.asset] = str(library_file)
            continue
        try:
            aid = uuid.UUID(overlay.asset)
        except ValueError:
            raise RenderError(
                "unknown_asset", f"overlay asset {overlay.asset} is not an asset id"
            ) from None
        asset = await db_ops.get(db, Asset, aid)
        if asset is None:
            raise RenderError("unknown_asset", f"no local file for overlay asset {overlay.asset}")
        asset_files[overlay.asset] = str(store.path_for(asset.storage_key))

    if timeline.music is not None:
        music_file = resolve_asset_file(settings.ASSETS_DIR, meme_dir, timeline.music.asset)
        if music_file is None:
            raise RenderError("unknown_asset", f"music {timeline.music.asset} not in library")
        asset_files[timeline.music.asset] = str(music_file)

    for hit in timeline.sfx:
        sfx_file = resolve_asset_file(settings.ASSETS_DIR, meme_dir, hit.asset)
        if sfx_file is None:
            raise RenderError("unknown_asset", f"sound effect {hit.asset} not in library")
        asset_files[hit.asset] = str(sfx_file)

    generated = [v.asset for v in timeline.voiceovers]
    if timeline.dub is not None:
        generated.append(timeline.dub.asset)
    for asset_id in generated:
        gen_file = resolve_asset_file(settings.ASSETS_DIR, meme_dir, asset_id)
        if gen_file is None:
            raise RenderError("unknown_asset", f"generated audio {asset_id} not in library")
        asset_files[asset_id] = str(gen_file)

    holder = {"db": 0.0}
    push = _progress_writer(db, jid, render)

    def _progress(frac: float) -> None:
        if frac - holder["db"] >= PROGRESS_STEP or frac >= 1.0:
            holder["db"] = frac
            push(round(frac, 3))

    with tempfile.TemporaryDirectory(prefix="reelforge-render-") as tmp:
        out = await render_timeline(
            timeline, str(master_path), asset_files, profile, store, tmp, _progress
        )
    key = str(out["key"])
    await db_ops.update(
        db,
        RenderRow,
        render.id,
        {
            "status": "done",
            "output_key": key,
            "output_url": store.public_url(key),
            "timeline_hash": str(out["timeline_hash"]),
            "attributions": attributions(timeline),
        },
    )
    await _set_job(db, jid, "done", 1.0)
    return {"status": "done", "output_key": key, "media_s": timeline.duration_s}
