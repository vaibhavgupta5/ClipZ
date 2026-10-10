"""Core data-model contract: 8 tables, license fields, uniqueness rules (no DB needed)."""

from app.models import Base

EXPECTED_TABLES = {
    "users",
    "projects",
    "assets",
    "timelines",
    "timeline_versions",
    "messages",
    "jobs",
    "renders",
}


def test_all_tables_present() -> None:
    assert EXPECTED_TABLES <= set(Base.metadata.tables)


def test_assets_carry_license_metadata() -> None:
    cols = Base.metadata.tables["assets"].columns
    for name in ("source", "license", "attribution_required", "attribution_text", "embedding"):
        assert name in cols, f"assets.{name} missing"


def test_jobs_idempotency_key_unique() -> None:
    table = Base.metadata.tables["jobs"]
    assert "idempotency_key" in table.columns
    uniques = {tuple(sorted(c.name for c in const.columns)) for const in table.constraints}
    assert ("idempotency_key",) in uniques


def test_timeline_versions_unique_per_timeline() -> None:
    table = Base.metadata.tables["timeline_versions"]
    uniques = {tuple(sorted(c.name for c in const.columns)) for const in table.constraints}
    assert ("timeline_id", "version") in uniques


def test_renders_link_to_timeline_hash() -> None:
    cols = Base.metadata.tables["renders"].columns
    for name in ("timeline_version_id", "timeline_hash", "profile", "status"):
        assert name in cols, f"renders.{name} missing"
