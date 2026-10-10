"""Analysis cache integration test: bundle store/fetch round-trip. Opt-in."""

import uuid

import pytest

from app import db as db_ops
from app.analysis.cache import load_cached, save_bundle
from app.analysis.models import AnalysisBundle, EnergyCurve
from app.analysis.models import cache_key as bundle_key
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
    db = db_ops.get_db()
    store = get_storage(get_settings())
    user = await db_ops.insert(db, User(api_key_hash=f"cache-{uuid.uuid4().hex}"))
    pid = (await db_ops.insert(db, Project(user_id=user.id, name="cache"))).id

    assert await load_cached(db, store, pid, "sha123", "full", "1", {}) is None
    key = await save_bundle(db, store, pid, "sha123", "full", "1", {}, _bundle())
    assert key == bundle_key("sha123", "full", "1", {})
    # saving the same bundle again (another worker) is a no-op, not an error
    assert await save_bundle(db, store, pid, "sha123", "full", "1", {}, _bundle()) == key

    hit = await load_cached(db, store, pid, "sha123", "full", "1", {})
    assert hit is not None and hit.language == "en"
    assert hit.analyzer_versions == {"vad": "energy-1"}
    miss = await load_cached(db, store, pid, "sha123", "full", "2", {})
    assert miss is None  # version is part of the key
