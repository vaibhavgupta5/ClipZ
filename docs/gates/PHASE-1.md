# Gate Report: PHASE-1 Timeline Core

Date: 2026-10-10. Plan: `docs/plans/PHASE-1.md`.

## Commands run
- `make check` → **89 passed** (unit), ruff + format + mypy strict clean.
- `pytest tests/unit/timeline tests/integration/test_timeline_versions.py --cov=app/timeline` → **100%**, 64 tests.
- `pytest -m integration` → 3 passed (storage round-trip, version persistence, full timeline API lifecycle).
- `make golden` → stub passes.

## What was built
- `app/timeline/`: transcript (Word/Beat), frozen EDL schema, 14 pure ops returning `(Timeline, Diff)`,
  snap (word IDs → output seconds through cuts + speed), validators with LLM-actionable `ToolError`s,
  immutable VersionLog + PG persistence (undo/redo = pointer moves, branch keeps history),
  deterministic LLM summary with inline snapshot test.
- API: `POST /projects`, `GET .../timeline`, `POST .../ops` (14-op discriminated union, 422s carry codes+hints),
  `GET .../versions`, `POST .../undo|redo`. Stub auth: X-API-Key → auto-provisioned user (Phase 10 JWT).
- Hypothesis properties: snap monotonicity, never-split-word boundaries, ops preserve validity.

## Deviations
- Schema = structure, validator = policy: license presence, speed/zoom/WPL ranges moved out of pydantic
  bounds into `validate.py` so every check is reachable + covered (100%).
- `errors.py` folded forward from Task 6 (ops needed `OpError`); `validate.py` folded forward (properties needed it).
- `save_version` stamps `tl.version` into the snapshot and returns `(version, stamped)`.

## Gaps / follow-ups
- No transcript source yet (Phase 3 analysis generates it; API accepts client-supplied transcripts for now).
- Clips reference asset IDs as opaque strings; `known_asset_ids` check is opt-in until Phase 2 ingest registers assets.
- `require_user` opens a second session per request (fine at this scale; revisit with request-scoped sessions in Phase 10).

## Verdict: GO for Phase 2.
