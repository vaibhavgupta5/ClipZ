# Gate Report: PHASE-2 Ingest

Date: 2026-10-10. Plan: `docs/plans/PHASE-2.md`.

## Commands run
- `make check` → **110 passed**, ruff + format + mypy strict clean.
- `pytest -m integration` → **14 passed** (storage, versions, timelines API, ingest matrix, uploads API).
- `make golden` → stub passes.
- End-to-end vs compose: presigned PUT → complete → arq worker → SSE `running…done` verified with real ffmpeg.
- Alembic: r2 `add assets.source_sha` applied (`upgrade head` green).

## Fixture matrix (all through the full pipeline)
- bars_30s: master CFR 30, 48 kHz stereo, duration ±40 ms; proxy 540px, keyframe gap ≤ 0.6 s, `-ss 2.5` seek exact.
- vfr (true VFR 150/7): normalized to CFR 30.
- rotated (landscape, no metadata — ffmpeg 9 CLI can't write rotate tags): passes through; limitation recorded in Phase-0 gate.
- silent_5s: accepted; silent 16 kHz mono ASR audio synthesized; waveform peaks all zero.
- mono_5s: upmixed to stereo.
- bars_91s → `too_long`; corrupt bytes → `probe_failed`; sha mismatch → rejected; idempotent re-run reuses assets (`reused: true`, row counts equal); no `/tmp/clipz-ingest-*` leaks.

## Security review (hunt-file-upload checklist)
- **FFmpeg HLS/concat SSRF (real finding, fixed):** container allowlist (`mov/mp4/mkv/webm/avi` families; playlists rejected with code `container`) + `-protocol_whitelist file` on every ffprobe/ffmpeg invocation.
- Filename: strict regex + UUID staging keys; project-bound key prefix on complete (no cross-project completion).
- Arg lists only (grep test `test_no_shell.py` in CI); temp dir per job with cleanup test; 1 GB / 4K / 120 fps caps; presigned URLs never logged (local paths only in stderr tails).

## Deviations
- `assets.source_sha` column added (migration r2): derived assets link to source bytes; powers ingest idempotency and future analysis caching.
- Presigned URLs embed `S3_PUBLIC_ENDPOINT` (host-mapped) while SDK uses internal `S3_ENDPOINT` — SigV4 host binding requires this split in compose.
- Per-request DB engines (no global cache): fixes cross-event-loop affinity under function-scoped test loops. Lifespan pooling deferred to Phase 10.
- Single presigned PUT (not multipart): S3 single PUT covers ≤ 5 GB; resumable multipart deferred to Phase 10.

## Gaps / follow-ups
- HDR tone-map path is code-covered by arg-builder tests but has no HDR fixture (no HDR source available; record with a phone for `fixtures/real/`).
- Rotation metadata application tested at arg level only (same fixture limitation as Phase 0).
- Real worker dequeue verified end-to-end; `make worker` runs the same image as `api`.

## Verdict: GO for Phase 3.
