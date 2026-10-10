"""Render worker: Timeline version -> preview/final mp4 in object storage."""

import asyncio
import tempfile
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db as db_module
from app.models import Asset, Job, TimelineVersion
from app.models import Render as RenderRow
from app.render.errors import RenderError
from app.render.render import profile_for, render_timeline
from app.settings import Settings
from app.storage import get_storage
from app.timeline.schema import Timeline as TimelineModel

PROGRESS_STEP = 0.1


def _write_bytes(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


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


def _push_progress(settings: Settings, job_id: uuid.UUID, frac: float) -> None:
    """Throttled progress write from sync callbacks (own short-lived engine)."""

    async def _update() -> None:
        engine = db_module.create_engine(settings)
        try:
            factory = db_module.session_factory(engine)
            async with factory() as session:
                job = await session.get(Job, job_id)
                if job is not None:
                    job.status = "running"
                    job.progress = frac
                    await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_update())


async def render_task(ctx: dict[str, Any], *, job_id: str, render_id: str) -> dict[str, object]:
    """Render the timeline snapshot linked to a Render row. Returns status dict."""
    settings: Settings = ctx["settings"]
    jid, rid = uuid.UUID(job_id), uuid.UUID(render_id)
    store = get_storage(settings)
    engine = db_module.create_engine(settings)
    try:
        factory = db_module.session_factory(engine)
        async with factory() as session:
            render = await session.get(RenderRow, rid)
            if render is None:
                raise ValueError(f"render {rid} does not exist")
            try:
                async with factory() as work:
                    result = await _run_render(work, store, settings, jid, render)
                return result
            except Exception as exc:
                async with factory() as work:
                    await _set_job(work, jid, "failed", 1.0, f"{type(exc).__name__}: {exc}")
                    failed = await work.get(RenderRow, rid)
                    if failed is not None:
                        failed.status = "failed"
                        await work.commit()
                return {"status": "failed", "error": str(exc)}
    finally:
        await engine.dispose()


async def _run_render(
    session: AsyncSession, store: Any, settings: Settings, jid: uuid.UUID, render: RenderRow
) -> dict[str, object]:
    version_row = await session.get(TimelineVersion, render.timeline_version_id)
    if version_row is None:
        raise ValueError("timeline version gone")
    timeline = TimelineModel.model_validate(dict(version_row.edl))
    profile = profile_for(render.profile)

    with tempfile.TemporaryDirectory(prefix="reelforge-render-") as tmp:
        master_asset = await session.scalar(
            select(Asset).where(Asset.project_id == render.project_id, Asset.kind == "master")
        )
        if master_asset is None:
            raise ValueError("no master asset (ingest incomplete?)")
        master_local = f"{tmp}/master.mp4"
        master_bytes = store.get_bytes(master_asset.storage_key)
        await asyncio.to_thread(_write_bytes, master_local, master_bytes)

        asset_files: dict[str, str] = {}
        for overlay in timeline.overlays:
            try:
                aid = uuid.UUID(overlay.asset)
            except ValueError:
                raise RenderError(
                    "unknown_asset", f"overlay asset {overlay.asset} is not an asset id"
                ) from None
            asset = await session.get(Asset, aid)
            if asset is None:
                raise RenderError(
                    "unknown_asset", f"no local file for overlay asset {overlay.asset}"
                )
            local = f"{tmp}/ov_{aid.hex}.bin"
            await asyncio.to_thread(_write_bytes, local, store.get_bytes(asset.storage_key))
            asset_files[overlay.asset] = local

        holder = {"db": 0.0}

        def _progress(frac: float) -> None:
            if frac - holder["db"] >= PROGRESS_STEP or frac >= 1.0:
                holder["db"] = frac
                _push_progress(settings, jid, round(frac, 3))

        out = await render_timeline(
            timeline, master_local, asset_files, profile, store, tmp, _progress
        )
        render.status = "done"
        render.output_url = store.presigned_download_url(str(out["key"]))
        render.timeline_hash = str(out["timeline_hash"])
        await session.commit()
    await _set_job(session, jid, "done", 1.0)
    return {"status": "done", "output_key": out["key"]}
