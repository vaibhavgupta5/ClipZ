"""FastAPI application factory with request-ID middleware."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from structlog import contextvars

from app.api.analysis import router as analysis_router
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.media import router as media_router
from app.api.metrics import router as metrics_router
from app.api.renders import router as renders_router
from app.api.timelines import router as timelines_router
from app.api.uploads import router as uploads_router
from app.db import get_db, migrate
from app.logging_setup import get_logger, setup_logging
from app.settings import Settings, get_settings
from app.storage import get_storage

log = get_logger(component="api")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app (injectable settings for tests)."""
    settings = settings or get_settings()
    setup_logging(settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        get_storage(settings).ensure_dirs()
        try:
            await migrate(get_db(settings))
        except Exception as exc:  # noqa: BLE001 - API still serves /healthz; /readyz reports db down
            log.warning("database migration failed", error=str(exc))
        yield

    app = FastAPI(title="ReelForge", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-ID"] = request_id
        return response

    app.include_router(health_router)
    app.include_router(timelines_router)
    app.include_router(uploads_router)
    app.include_router(analysis_router)
    app.include_router(renders_router)
    app.include_router(media_router)
    app.include_router(chat_router)
    app.include_router(metrics_router)
    return app


app = create_app()
