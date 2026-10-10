"""Analysis cache integration test: bundle store/fetch round-trip. Opt-in."""

import uuid

import pytest
from sqlalchemy import delete

from app.analysis.cache import load_cached, save_bundle
from app.analysis.models import AnalysisBundle, EnergyCurve
from app.analysis.models import cache_key as bundle_key
from app.db import create_engine, session_factory
from app.models import Project, User
from app.settings import get_settings
from app.storage import get_storage
from app.timeline.transcript import Transcript, Word

pytestmark = pytest.mark.integration


def _bundle() -> AnalysisBundle:
    return AnalysisBundle(
        transcript=Transcript(
            words=[Word(id="w1", start=0.0, end=0.4, text="a", display_text="a", lang="en")]
        ),
        energy=EnergyCurve(bucket_ms=100, values=[0.5]),
        language="en",
        analyzer_versions={"vad": "energy-1"},
    )


async def test_cache_miss_then_hit() -> None:
    engine = create_engine(get_settings())
    store = get_storage(get_settings())
    user_id = None
    try:
        factory = session_factory(engine)
        async with factory() as session:
            user = User(api_key_hash=f"cache-{uuid.uuid4().hex}")
            session.add(user)
            await session.flush()
            user_id = user.id
            project = Project(user_id=user.id, name="cache")
            session.add(project)
            await session.flush()
            pid = project.id
            await session.commit()

        async with factory() as session:
            assert await load_cached(session, store, pid, "sha123", "full", "1", {}) is None
            key = await save_bundle(session, store, pid, "sha123", "full", "1", {}, _bundle())
            assert key == bundle_key("sha123", "full", "1", {})
            await session.commit()

        async with factory() as session:
            hit = await load_cached(session, store, pid, "sha123", "full", "1", {})
            assert hit is not None and hit.language == "en"
            assert hit.analyzer_versions == {"vad": "energy-1"}
            miss = await load_cached(session, store, pid, "sha123", "full", "2", {})
            assert miss is None  # version is part of the key

        async with engine.begin() as conn:
            from app.models import Analysis

            await conn.execute(delete(Analysis).where(Analysis.project_id == pid))
            await conn.execute(delete(Project).where(Project.id == pid))
            await conn.execute(delete(User).where(User.id == user_id))
    finally:
        await engine.dispose()
