"""Integration isolation: a throwaway Postgres schema and temp storage roots per test.

Runs against the local TimescaleDB container (same engine as Tiger Cloud); never the
cloud service.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app import db as db_ops
from app.settings import get_settings

LOCAL_TIGER = "postgresql://reelforge:reelforge@localhost:5433/reelforge"


@pytest.fixture(autouse=True)
async def isolated_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Fresh schema it_<random> with all migrations; dropped afterwards."""
    schema = f"it_{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("TIGER_DATABASE_URL", LOCAL_TIGER)
    monkeypatch.setenv("TIGER_SCHEMA", schema)
    for var, sub in (("UPLOAD_DIR", "upload"), ("WORK_DIR", "work"), ("EDITED_DIR", "edited")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    get_settings.cache_clear()
    db = db_ops.get_db(get_settings())
    await db_ops.migrate(db)
    yield
    # Unschedule this schema's TimescaleDB policy jobs first, or DROP races them.
    await db.execute(
        "SELECT delete_job(job_id) FROM timescaledb_information.jobs WHERE hypertable_schema = $1",
        schema,
    )
    for attempt in range(3):
        try:
            await db.execute(f'DROP SCHEMA "{schema}" CASCADE')
            break
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(0.5)
    await db.close()
    get_settings.cache_clear()
