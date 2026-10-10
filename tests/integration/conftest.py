"""Integration isolation: a throwaway Mongo database and temp storage roots per test."""

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app import db as db_ops
from app.settings import get_settings


@pytest.fixture(autouse=True)
async def isolated_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Point settings at reelforge_it_<random> and tmp dirs; drop the database afterwards."""
    name = f"reelforge_it_{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("MONGODB_DB", name)
    for var, sub in (("UPLOAD_DIR", "upload"), ("WORK_DIR", "work"), ("EDITED_DIR", "edited")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    get_settings.cache_clear()
    settings = get_settings()
    await db_ops.ensure_indexes(db_ops.get_db(settings))
    yield
    await db_ops.client_for(settings).drop_database(name)
    get_settings.cache_clear()
