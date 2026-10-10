# PHASE-1 Implementation Plan: Timeline Core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Timeline/EDL model, versioning, word-ID snapping, fully tested. No rendering.

**Architecture:** Pydantic v2 frozen models (immutable by construction); pure op functions `Timeline -> (Timeline, Diff)`; `snap.py` maps word IDs to output seconds through the cut+speed list; `validate.py` returns LLM-actionable errors; `versions.py` = pure pointer log + thin async PG persistence; `summary.py` compacts for LLM context; FastAPI CRUD in `app/api/timelines.py` with stub API-key auth.

**Tech Stack:** pydantic v2, SQLAlchemy 2 (persistence only), hypothesis (property tests), pytest-cov (95%+ gate on app/timeline).

**Spec:** `docs/BLUEPRINT.md` §§2.2, 3.3, 9; phase tasks from `prompt.md` §6 Phase 1.

## Global Constraints

- Anchors are word/beat IDs, never seconds. Padding default 100 ms (80–150 ms allowed range).
- Every op returns `(new_timeline, diff)`; inputs never mutated (frozen models + tests asserting input unchanged).
- Validation errors carry `code`, `field`, `message`, `hint` for LLM self-correction.
- No network in unit tests; DB persistence tests marked `integration`.
- `make check` green + `pytest --cov=app/timeline --cov-fail-under=95` before gate.

## Review Focus

- Speed ≠ 1 clips breaking snap monotonicity (output mapping must divide by speed) → property test in Task 4.
- delete_words at clip edges producing zero-length clips (must drop, not keep) → edge tests in Task 3.
- Branch-after-undo losing redo history silently (must keep rows, pointer move only) → test in Task 5.
- 90.0 s exactly allowed, 90.001 s rejected (float care: round durations to ms) → test in Task 6.
- Summary snapshot drifting silently (snapshot is a contract with the Director prompt) → inline-literal snapshot test in Task 7.

---

### Task 1: Transcript models (words, beats)

**Files:** Create `app/timeline/transcript.py`. Test: `tests/unit/timeline/test_transcript.py`.

**Interfaces:** Produces `Word(id, start, end, text, display_text, lang, confidence)`, `Beat(id, word_ids, intent, query_hint, tone)`, `Transcript(words, beats, language)` with validators (end > start; beat word_ids non-empty + exist; stable id format `w\d+`/`b\d+`).

- [ ] Step 1: Failing tests — valid transcript builds; end<start rejected; beat referencing unknown word rejected; bad id format rejected.
- [ ] Step 2: Run, verify FAIL (missing module).
- [ ] Step 3: Implement frozen pydantic models with validators + LLM-actionable messages.
- [ ] Step 4: Run tests + make check. PASS.
- [ ] Step 5: Commit `feat: add transcript word/beat models`.

### Task 2: Timeline schema

**Files:** Create `app/timeline/schema.py` (imports transcript). Test: `tests/unit/timeline/test_schema.py`.

**Interfaces:** Consumes `Transcript`. Produces `VideoClip(id, asset, src_in, src_out, speed)`, `CaptionConfig`, `Zoom(anchor_word, scale, dur_ms)`, `Overlay(id, type, anchor_word, offset_ms, duration_ms, layout, license_*)`, `MusicBed(asset?, license_*)`, `SfxHit`, `OutputSpec(aspect, res, fps)`, `Timeline(project_id, transcript, video_clips, audio_fx, captions, zooms, overlays, music, sfx, output, style_id?, trend_pack?)`, `Timeline.duration_s` (sum clip lens / speed, ms-rounded), `Diff(op, details)`.

- [ ] Steps 1–2: Failing tests (build minimal valid timeline; src_out<=src_in rejected; speed<=0 rejected; duration math with speed=2 halves length).
- [ ] Step 3: Implement.
- [ ] Step 4: Verify PASS + check.
- [ ] Step 5: Commit `feat: add timeline EDL schema`.

### Task 3: Ops (pure functions)

**Files:** Create `app/timeline/ops.py`. Test: `tests/unit/timeline/test_ops.py` (+ property tests Task 4).

**Interfaces:** Consumes `Timeline`, `Transcript`. Produces `trim(tl, start_word, end_word, pad_ms=100)`, `split(tl, at_word)`, `delete_range(tl, start_word, end_word)`, `delete_words(tl, word_ids)`, `reorder(tl, clip_ids)`, `set_speed(tl, start_word, end_word, factor)`, `add_overlay/remove_overlay`, `add_zoom/remove_zoom`, `set_captions`, `set_music`, `add_sfx/remove_sfx` — each `-> tuple[Timeline, Diff]`, never mutating input; zero-length clips dropped; cuts snap outward to word edges.

- [ ] Steps 1–2: Failing tests per op incl. edge: delete_words at clip edges; trim to single word; reorder with unknown id rejected; set_speed factor bounds (0.25–4).
- [ ] Step 3: Implement (helpers: `_clip_index_at`, `_snap_outward` shared with snap.py).
- [ ] Step 4: Verify PASS + check.
- [ ] Step 5: Commit `feat: add pure timeline ops`.

### Task 4: Snapping + property tests

**Files:** Create `app/timeline/snap.py`. Test: `tests/unit/timeline/test_snap.py`, `tests/unit/timeline/test_properties.py`.

**Interfaces:** Produces `output_time_of(tl, word_id) -> float` (source→output through cuts+speed), `output_range_of(tl, start_word, end_word, pad_ms)`, `snap_boundary(tl, source_t, direction)`. Properties (hypothesis): output times strictly increase with word order; no resolved boundary falls strictly inside a word; op validity preserved (valid in → valid out).

- [ ] Steps 1–2: Failing unit tests (two-clip map; speed clip maps correctly; pad clamps at neighbors).
- [ ] Step 3: Implement + hypothesis strategies (small word lists, random cut sets).
- [ ] Step 4: Verify (hypothesis deadlines generous; `--hypothesis-seed=0` in CI? leave default).
- [ ] Step 5: Commit `feat: add word-ID snapping with property tests`.

### Task 5: Versioning (pure log + PG persistence)

**Files:** Create `app/timeline/versions.py`. Test: `tests/unit/timeline/test_versions.py` (pure log), `tests/integration/test_timeline_versions.py` (PG round-trip via compose db).

**Interfaces:** Produces `VersionLog` (append/edl, undo, redo, branch-on-edit-after-undo keeping all rows with parent links) + async `save_version(session, timeline_id, edl, parent)`, `get_version`, `list_versions`, `set_current`. Undo/redo = pointer moves on `timelines.current_version`.

- [ ] Steps 1–2: Failing tests (append→v1,v2; undo→pointer 1; new edit→v3 parent=1, rows kept; redo cleared after branch).
- [ ] Step 3: Implement pure log + async repo (SQLAlchemy `timeline_versions`/`timelines` tables from Phase 0).
- [ ] Step 4: Unit PASS; integration PASS vs compose db.
- [ ] Step 5: Commit `feat: add timeline versioning with undo/redo`.

### Task 6: Validators

**Files:** Create `app/timeline/validate.py`, `app/timeline/errors.py` (shared `ToolError(code, field, message, hint)`). Test: `tests/unit/timeline/test_validate.py`.

**Interfaces:** Produces `validate_timeline(tl, *, max_seconds=90, known_asset_ids=None) -> list[ToolError]` + `ensure_valid(...)` raising `TimelineInvalid`. Checks: duration ≤ 90 s (ms-rounded); no same-track overlaps; anchors exist in transcript; overlay/music/sfx license present; words_per_line 1..4; zoom scale 1.0–2.0; known_asset_ids membership when provided.

- [ ] Steps 1–2: Failing tests per check (90.0 ok / 90.001 rejected; overlap; dangling anchor; missing license; wpl=5).
- [ ] Step 3: Implement with LLM-actionable hints (name the valid range + offending value).
- [ ] Step 4: Verify.
- [ ] Step 5: Commit `feat: add timeline validators`.

### Task 7: LLM summary + snapshot

**Files:** Create `app/timeline/summary.py`. Test: `tests/unit/timeline/test_summary.py` (inline-literal snapshot).

**Interfaces:** Produces `summarize(tl) -> str` (~15 lines: duration, clips, cuts/min, speed segs, captions mode/position, zooms, overlays w/ anchors, music/sfx presence, style) — token-efficient, deterministic key order.

- [ ] Steps 1–2: Failing snapshot test with exact expected string for a fixture timeline.
- [ ] Step 3: Implement.
- [ ] Step 4: Verify.
- [ ] Step 5: Commit `feat: add timeline LLM summary`.

### Task 8: Timeline CRUD API

**Files:** Create `app/api/timelines.py` (+ `app/api/deps.py` stub auth), wire router in `app/main.py`. Test: `tests/integration/test_timelines_api.py` (httpx vs compose: create project, apply ops, undo/redo, 422 on bad op).

**Interfaces:** `POST /projects` (X-API-Key → get-or-create stub user), `GET /projects/{id}/timeline`, `POST /projects/{id}/ops` (discriminated op union → new version + diff), `GET .../versions`, `POST .../undo`, `POST .../redo`. 422 body = list[ToolError].

- [ ] Steps 1–2: Failing integration tests (need transcript fixture: build from a 10-word synthetic transcript in-test).
- [ ] Step 3: Implement (session per request; validate before persist; bump current_version).
- [ ] Step 4: Verify vs compose stack.
- [ ] Step 5: Commit `feat: add timeline CRUD API`.

### Task 9: Gate

- [ ] `pytest --cov=app/timeline --cov-fail-under=95` (add pytest-cov to dev deps); mypy strict clean.
- [ ] Write `docs/gates/PHASE-1.md`, update PROGRESS.md.
- [ ] Commit `docs: phase 1 gate report`.
