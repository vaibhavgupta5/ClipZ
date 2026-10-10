# PHASE-2 Implementation Plan: Ingest, Normalization, Proxies

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload → validated → normalized master + proxy + ASR audio + waveform, with progress events.

**Architecture:** API issues presigned URLs (never proxies bytes) and enqueues Arq jobs; `app/media/` holds pure probe-parse/validation/ffmpeg-arg builders (unit-tested, no subprocess); `app/workers/ingest.py` runs the pipeline in an isolated temp dir, updating the `jobs` row; SSE endpoint tails the job row. ffmpeg/ffprobe invoked via arg lists only.

**Tech Stack:** ffmpeg 9 CLI (args: `-fps_mode`, never `-vsync`; `-display_rotation` is input-only), numpy (waveform peaks), sse-starlette (progress stream), arq (queue).

**Spec:** `docs/BLUEPRINT.md` §§7.1, 10 (iPhone/VFR/HDR pitfalls); phase tasks from `prompt.md` §6 Phase 2.

## Global Constraints

- No `shell=True`, no string-built filtergraphs with external input — arg lists only (grep test).
- Caps: duration ≤ 90 s (locked), file ≤ 1 GB, resolution ≤ 3840×2160, fps ≤ 120, HDR tone-mapped, rotation applied.
- Idempotency: `jobs.idempotency_key = ingest:{sha256}`; assets reused by `(sha256, kind)`.
- Storage layout: `{project}/raw/{sha}.mp4`, `master.mp4`, `proxy.mp4`, `asr.wav`, `waveform.json`.
- Silent videos accepted (empty speech, not an error); mono accepted (upmixed).

## Review Focus

- VFR timebase drift in normalize (must force CFR 30 via `fps=30`, not just `-r 30`) → assert on vfr fixture.
- Rotation applied exactly once (probe tag + transpose, then strip metadata) → synthetic-probe unit test + landscape assertion.
- 90.0 s accepted / 90.001 s rejected at the ingest gate (same ms rule as validator) → test with bars_89s/bars_91s.
- Presigned complete for a key the server didn't issue (path forgery) → key must match `uploads/{uuid}/...` pattern issued for that project.
- Worker temp dir leak on failure (must clean up in finally) → test via unit-level context manager.

---

### Task 1: Media models + ffprobe wrapper

**Files:** Create `app/media/__init__.py`, `app/models.py` (`MediaInfo`, `IngestError`), `probe.py`. Test: `tests/unit/media/test_probe.py` (+ `__init__.py`s).

**Interfaces:** Produces `probe_file(path) -> MediaInfo` (arg-list ffprobe JSON, timeout 60 s, `MediaError` on failure); `MediaInfo{duration_s, width, height, fps, avg_fps, vcodec, acodec|None, audio_channels, sample_rate, rotation_deg, color_transfer, size_bytes}`. Pure `parse_probe_json(obj, size_bytes)`.

- [ ] Step 1: Failing tests — parse a checked-in ffprobe JSON (synthetic VFR + rotated-tag variants); missing video stream → IngestError("no_video").
- [ ] Step 2: Run, FAIL (missing module).
- [ ] Step 3: Implement (rotation from `rotate` tag OR displaymatrix side_data; fps from r_frame_rate).
- [ ] Step 4: PASS + check. Step 5: Commit `feat: add ffprobe wrapper and media models`.

### Task 2: Validation + ffmpeg arg builders (no subprocess)

**Files:** Create `app/media/validate.py`, `app/media/ffmpeg.py`, `app/media/normalize.py`. Test: `tests/unit/media/test_ingest_rules.py`.

**Interfaces:** `validate_info(info, max_seconds=90) -> list[IngestError-code]`; `normalize_args(src, dst, info)`, `proxy_args(src, dst)`, `asr_audio_args(src, dst)` returning arg LISTS; `run_ffmpeg(args, timeout_s) -> None` (arg list, captures stderr tail on failure). HDR filter only when `color_transfer in {smpte2084, arib-std-b67}`; transpose only when rotation != 0.

- [ ] Steps 1–2: Failing tests — 91 s rejected / 89 s ok; 8K rejected; no-audio ok; HDR args contain `tonemap`; rotated args contain `transpose`; vfr normalize contains `fps=30`.
- [ ] Step 3: Implement. Step 4: PASS + check. Step 5: Commit `feat: add ingest validation and ffmpeg builders`.

### Task 3: Waveform peaks

**Files:** Create `app/media/waveform.py` (+ numpy dep). Test: `tests/unit/media/test_waveform.py` (synthetic PCM bytes, no ffmpeg).

**Interfaces:** `peaks_from_pcm(samples: bytes, sample_rate: int, bucket_ms=100) -> list[float]` (0..1); `waveform_json(peaks, duration_s) -> dict`.

- [ ] Steps 1–2: Failing tests (silence → zeros; full-scale sine → ~1.0; bucket count math).
- [ ] Step 3: Implement (numpy, little-endian f32/int16 support). Step 4: PASS + check. Step 5: Commit `feat: add waveform peak computation`.

### Task 4: Ingest worker task

**Files:** Create `app/workers/ingest.py`, `app/workers/queue.py`; register in `WorkerSettings`. Test: `tests/integration/test_ingest.py` (real ffmpeg vs compose: bars_30s full pipeline asserts; bars_91s rejected; corrupt rejected; idempotent re-run reuses assets; temp dir cleaned).

**Interfaces:** `ingest_asset(ctx, *, job_id, project_id, storage_key, sha256)` stages: download→probe→validate→normalize→proxy→asr audio+waveform→asset rows→job done; progress 0.1/0.5/0.8/0.9/1.0 on jobs row; `enqueue_ingest(redis_url, payload) -> job_id`.

- [ ] Steps 1–2: Failing integration tests (need worker code path but NOT a running worker: call `ingest_asset` directly with fake ctx — no Redis needed).
- [ ] Step 3: Implement (tempfile.TemporaryDirectory under `/tmp/reelforge-ingest-*`; finally-cleanup; per-stage progress fn).
- [ ] Step 4: PASS vs fixtures. Step 5: Commit `feat: add ingest worker pipeline`.

### Task 5: Upload API + SSE progress

**Files:** Create `app/api/uploads.py`; wire router; add sse-starlette dep. Test: extend `tests/integration/test_timelines_api.py`? New `tests/integration/test_uploads_api.py` (presigned URL shape; complete rejects foreign key; SSE receives terminal `done`).

**Interfaces:** `POST /projects/{id}/uploads {filename, content_type, size_bytes} -> {key, upload_url}` (key `uploads/{project}/{uuid}/{safe filename}`; size cap 1 GB); `POST .../uploads/complete {key, sha256} -> {job_id}` (verifies object exists + size match, creates raw asset + job, enqueues); `GET .../jobs/{job_id}/events` SSE (`status`, `progress`, terminal `done|failed`). Multipart: single PUT URL (multipart upload is Phase 10; presigned PUT suffices for ≤1 GB? S3 single PUT ≤ 5 GB — fine, note it).

- [ ] Steps 1–2: Failing tests.
- [ ] Step 3: Implement. Step 4: PASS. Step 5: Commit `feat: add upload API and ingest progress SSE`.

### Task 6: Security review (hunt-file-upload checklist)

**Files:** None (review only). Skill: `hunt-file-upload` as checklist + grep test `tests/unit/test_no_shell.py` asserting no `shell=True|os.system|subprocess.*shell` in `app/`.

**Interfaces:** Review: temp isolation, key validation, size caps, arg-list-only invocation, stderr secret-scrub (presigned URLs must never land in logs), decompression-bomb caps (pixels × fps × duration ceiling).

- [ ] Step 1: Load skill, run checklist against app/media + app/api/uploads + app/workers/ingest.
- [ ] Step 2: Add grep test; fix findings.
- [ ] Step 3: Commit `test: add no-shell enforcement and upload hardening` (or fold into Task 5 commit if clean).

### Task 7: Gate

- [ ] Fixture matrix vs pipeline: normal/VFR/rotated/silent/mono/91s-reject/corrupt-reject; ffprobe asserts (CFR 30, orientation applied, 48 kHz stereo, duration ±40 ms); proxy keyframe interval ≤ 0.5 s + accurate seek.
- [ ] `make check` + integration green; write `docs/gates/PHASE-2.md`; update PROGRESS.md; commit `docs: phase 2 gate report`.
