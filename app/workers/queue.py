"""Queue helpers: enqueue Arq jobs from the API (Redis payloads stay tiny)."""

from typing import Any

from arq import create_pool
from arq.connections import RedisSettings


async def enqueue_ingest(redis_url: str, payload: dict[str, Any]) -> str:
    """Enqueue ingest_asset; returns the arq job id (our jobs row id is in the payload)."""
    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        job = await pool.enqueue_job("ingest_asset", **payload)
        if job is None:
            raise RuntimeError("arq refused the ingest job")
        return str(job.job_id)
    finally:
        await pool.close()


async def enqueue_render(redis_url: str, payload: dict[str, Any]) -> str:
    """Enqueue render_task; returns the arq job id."""
    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        job = await pool.enqueue_job("render_task", **payload)
        if job is None:
            raise RuntimeError("arq refused the render job")
        return str(job.job_id)
    finally:
        await pool.close()
