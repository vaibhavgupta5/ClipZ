"""Render worker: Timeline version -> preview/final mp4 under editedVideo/<project>/.

A final export ends the project's working set: ./work/<project>/ is deleted
afterwards (ADR-010). Previews never delete anything.
"""

import tempfile
import uuid
from typing import Any

from pymongo import MongoClient

from app import db as db_ops
from app.db import Database
from app.models import Asset, Job, TimelineVersion
from app.models import Render as RenderRow
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


def _push_progress(settings: Settings, job_id: uuid.UUID, frac: float) -> None:
    """Progress write from sync ffmpeg callbacks (sync client; no event loop needed)."""
    client: MongoClient[dict[str, Any]] = MongoClient(
        settings.MONGODB_URL, uuidRepresentation="standard"
    )
    with client:
        client[settings.MONGODB_DB][Job.collection].update_one(
            {"_id": job_id}, {"$set": {"status": "running", "progress": frac}}
        )


async def render_task(ctx: dict[str, Any], *, job_id: str, render_id: str) -> dict[str, object]:
    """Render the timeline snapshot linked to a Render document. Returns a status dict."""
    settings: Settings = ctx["settings"]
    jid, rid = uuid.UUID(job_id), uuid.UUID(render_id)
    db = db_ops.get_db(settings)
    render = await db_ops.get(db, RenderRow, rid)
    if render is None:
        raise ValueError(f"render {rid} does not exist")
    try:
        return await _run_render(db, get_storage(settings), settings, jid, render)
    except Exception as exc:
        await _set_job(db, jid, "failed", 1.0, f"{type(exc).__name__}: {exc}")
        await db_ops.update(db, RenderRow, rid, {"status": "failed"})
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
    for overlay in timeline.overlays:
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

    holder = {"db": 0.0}

    def _progress(frac: float) -> None:
        if frac - holder["db"] >= PROGRESS_STEP or frac >= 1.0:
            holder["db"] = frac
            _push_progress(settings, jid, round(frac, 3))

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
        },
    )
    await _set_job(db, jid, "done", 1.0)
    if profile.name == "final":
        store.delete_tree(f"projects/{render.project_id}")
    return {"status": "done", "output_key": key}
