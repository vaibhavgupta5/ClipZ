"""Arq CPU worker entrypoint. Job functions land here in later phases."""

from typing import Any

from arq.connections import RedisSettings

from app.logging_setup import get_logger, setup_logging
from app.settings import get_settings

log = get_logger(component="worker")


async def startup(ctx: dict[str, Any]) -> None:
    """Worker startup: logging + settings into ctx."""
    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    ctx["settings"] = settings
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

    functions = [health_ping]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().REDIS_URL)
