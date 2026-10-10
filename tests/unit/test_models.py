"""Data-model contract: every model has a table whose columns match it (no DB needed)."""

import re
from pathlib import Path

from app.db import _col, _order, _where
from app.models import Asset, Doc, Job, Render

SQL = "\n".join(p.read_text() for p in sorted(Path("app/sql").glob("*.sql")))


def _table_columns(table: str) -> set[str]:
    body = re.search(rf"CREATE TABLE {table} \((.*?)\n\)", SQL, re.S)
    assert body, f"no CREATE TABLE for {table}"
    cols = set()
    for line in body.group(1).splitlines():
        m = re.match(r"\s+([a-z0-9_]+)\s+[a-z]", line)
        if m and m.group(1) not in ("primary", "unique"):
            cols.add(m.group(1))
    return cols


def test_every_model_has_a_matching_table() -> None:
    models = Doc.__subclasses__()
    assert len(models) >= 13
    for model in models:
        assert _table_columns(model.collection) == set(model.model_fields), model.__name__


def test_assets_carry_license_metadata() -> None:
    for name in ("source", "license", "attribution_required", "attribution_text", "commercial_use"):
        assert name in Asset.model_fields
    assert "license               text NOT NULL" in SQL


def test_uniqueness_rules_exist() -> None:
    assert "idempotency_key  text NOT NULL UNIQUE" in SQL
    assert "UNIQUE (timeline_id, version)" in SQL
    assert "UNIQUE (source_sha, analyzer, version, params_hash)" in SQL


def test_telemetry_tables_are_hypertables_with_rollups() -> None:
    for t in ("agent_runs", "tool_calls", "pipeline_stages", "render_progress"):
        assert re.search(rf"CREATE TABLE {t} \(.*?tsdb\.hypertable", SQL, re.S), t
    for v in ("agent_runs_hourly", "tool_calls_hourly", "pipeline_stages_hourly"):
        assert f"CREATE MATERIALIZED VIEW {v}" in SQL


def test_query_builder_rejects_unknown_columns_and_binds_values() -> None:
    import pytest

    where, args = _where(Job, {"_id": 1, "status": "done", "error": None})
    assert where == ' WHERE "id" = $1 AND "status" = $2 AND "error" IS NULL' and args == [1, "done"]
    assert _order(Render, [("created_at", -1)]) == ' ORDER BY "created_at" DESC'
    with pytest.raises(ValueError):
        _col(Job, 'status"; DROP TABLE jobs; --')
