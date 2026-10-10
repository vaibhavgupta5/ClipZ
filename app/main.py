"""FastAPI application factory with request-ID middleware."""

import uuid

from fastapi import FastAPI, Request
from structlog import contextvars

from app.api.health import router as health_router
from app.api.timelines import router as timelines_router
from app.logging_setup import get_logger, setup_logging
from app.settings import Settings, get_settings

log = get_logger(component="api")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app (injectable settings for tests)."""
    settings = settings or get_settings()
    setup_logging(settings.LOG_LEVEL)

    app = FastAPI(title="ReelForge", version="0.1.0")

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
    return app


app = create_app()
