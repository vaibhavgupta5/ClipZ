"""Arq CPU worker entrypoint. Job functions land here in later phases."""

from typing import Any

from arq import func
from arq.connections import RedisSettings

from app.db import get_db, migrate
from app.logging_setup import get_logger, setup_logging
from app.settings import get_settings
from app.storage import get_storage
from app.workers.analyze import analyze_asset
from app.workers.dub import dub_task
from app.workers.ingest import ingest_asset
from app.workers.render import render_task

log = get_logger(component="worker")


async def startup(ctx: dict[str, Any]) -> None:
    """Worker startup: logging + settings into ctx."""
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    ctx["settings"] = settings
    get_storage(settings).ensure_dirs()
    await migrate(get_db(settings))
    log.info("worker startup")


async def shutdown(ctx: dict[str, Any]) -> None:
    """Worker shutdown."""
    log.info("worker shutdown")


async def health_ping(ctx: dict[str, Any]) -> dict[str, str]:
    """Queue smoke test: enqueue me and expect {'status': 'ok'} back."""
    _ = ctx
    return {"status": "ok"}


class WorkerSettings:
    """Arq worker config (functions registered per phase)."""

    # Long-running media jobs get explicit budgets (arq's default is 300 s).
    functions = [
        health_ping,
        func(ingest_asset, timeout=600),
        func(analyze_asset, timeout=1200),
        func(render_task, timeout=1800),
        func(dub_task, timeout=1200),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().REDIS_URL)
