"""Liveness + readiness probes. /readyz reflects DB/Redis/storage truthfully."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import db as db_module
from app.settings import get_settings
from app.storage import get_storage

router = APIRouter()


async def check_db() -> str:
    """SELECT 1 against Postgres; raises on failure."""
    return await db_module.check_connectivity(get_settings())


async def check_redis() -> str:
    """PING Redis; raises on failure."""
    import redis.asyncio as aioredis

    client = aioredis.from_url(get_settings().REDIS_URL, socket_connect_timeout=3)  # type: ignore[no-untyped-call]
    try:
        await client.ping()
    finally:
        await client.aclose()
    return "ok"


async def check_storage() -> str:
    """HEAD the bucket; raises on failure."""
    store = get_storage(get_settings())
    if not store.bucket_exists():
        raise ConnectionError("storage bucket unreachable")
    return "ok"


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: no dependencies."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> JSONResponse:
    """Readiness: run each checker directly so partial failures render per-check detail."""
    checkers: dict[str, Callable[[], Awaitable[str]]] = {
        "db": check_db,
        "redis": check_redis,
        "storage": check_storage,
    }
    checks: dict[str, str] = {}
    for name, fn in checkers.items():
        try:
            checks[name] = await fn()
        except Exception as exc:  # noqa: BLE001 - readiness must never 500
            checks[name] = f"down: {type(exc).__name__}"
    healthy = all(v == "ok" for v in checks.values())
    payload: dict[str, Any] = {
        "status": "ready" if healthy else "degraded",
        "checks": checks,
    }
    return JSONResponse(status_code=200 if healthy else 503, content=payload)
