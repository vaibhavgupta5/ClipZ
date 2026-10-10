"""Core data-model contract: collections, license fields, uniqueness rules (no DB needed)."""

import uuid
from typing import Any

from app.db import ensure_indexes
from app.models import Asset, Doc, Job, Render, TimelineVersion

EXPECTED = {
    "users",
    "projects",
    "assets",
    "timelines",
    "timeline_versions",
    "messages",
    "jobs",
    "renders",
    "analyses",
    "agent_runs",
}


class _FakeColl:
    def __init__(self, name: str, log: list[tuple[str, Any, bool]]) -> None:
        self.name, self.log = name, log

    async def create_index(self, keys: Any, unique: bool = False) -> str:
        self.log.append((self.name, keys, unique))
        return "ix"


class _FakeDb:
    def __init__(self) -> None:
        self.log: list[tuple[str, Any, bool]] = []

    def __getitem__(self, name: str) -> _FakeColl:
        return _FakeColl(name, self.log)


def _unique(log: list[tuple[str, Any, bool]], name: str) -> list[Any]:
    return [keys for coll, keys, unique in log if coll == name and unique]


def test_all_collections_declared() -> None:
    assert {c.collection for c in Doc.__subclasses__()} == EXPECTED


def test_assets_carry_license_metadata() -> None:
    for name in ("source", "license", "attribution_required", "attribution_text", "commercial_use"):
        assert name in Asset.model_fields, f"assets.{name} missing"


def test_id_round_trips_as_mongo_id() -> None:
    job = Job(type="ingest", idempotency_key="k")
    raw = job.to_mongo()
    assert isinstance(raw["_id"], uuid.UUID) and "id" not in raw
    assert Job.model_validate(raw).id == job.id


async def test_unique_indexes() -> None:
    db = _FakeDb()
    await ensure_indexes(db)  # type: ignore[arg-type]
    assert _unique(db.log, Job.collection) == ["idempotency_key"]
    assert _unique(db.log, TimelineVersion.collection) == [[("timeline_id", 1), ("version", 1)]]
    assert _unique(db.log, "users") == ["api_key_hash"]
    assert _unique(db.log, "timelines") == ["project_id"]


def test_renders_link_to_timeline_hash() -> None:
    for name in ("timeline_version_id", "timeline_hash", "profile", "status", "output_key"):
        assert name in Render.model_fields, f"renders.{name} missing"
