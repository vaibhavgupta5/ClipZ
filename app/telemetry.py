"""Telemetry writers for the Tiger Data hypertables (ADR-016).

Every writer swallows and logs its own errors: losing a metric must never fail an
edit, a render or a chat turn.
"""

import uuid
from collections.abc import Awaitable

from app import db as db_ops
from app.db import Database
from app.logging_setup import get_logger
from app.models import Doc, PipelineStage, RenderProgress, ToolCallLog

log = get_logger(component="telemetry")


async def _safe(write: Awaitable[Doc]) -> None:
    try:
        await write
    except Exception as exc:  # noqa: BLE001 - telemetry is best effort by design
        log.warning("telemetry write failed", error=f"{type(exc).__name__}: {exc}")


async def tool_call(
    db: Database,
    project_id: uuid.UUID,
    run_id: uuid.UUID | None,
    tool: str,
    ok: bool,
    latency_ms: int,
    error_code: str | None = None,
) -> None:
    """One Director tool call."""
    await _safe(
        db_ops.insert(
            db,
            ToolCallLog(
                run_id=run_id,
                project_id=project_id,
                tool=tool,
                ok=ok,
                error_code=error_code,
                latency_ms=latency_ms,
            ),
        )
    )


async def stage(
    db: Database,
    stage_name: str,
    duration_ms: int,
    *,
    project_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    ok: bool = True,
    media_s: float = 0.0,
) -> None:
    """One finished pipeline stage (ingest, analyze, render-preview, render-final...)."""
    await _safe(
        db_ops.insert(
            db,
            PipelineStage(
                project_id=project_id,
                job_id=job_id,
                stage=stage_name,
                ok=ok,
                duration_ms=duration_ms,
                media_s=media_s,
            ),
        )
    )


async def render_tick(
    db: Database, render_id: uuid.UUID, project_id: uuid.UUID | None, profile: str, progress: float
) -> None:
    """One render progress tick."""
    await _safe(
        db_ops.insert(
            db,
            RenderProgress(
                render_id=render_id, project_id=project_id, profile=profile, progress=progress
            ),
        )
    )
