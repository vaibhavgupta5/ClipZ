# Gate Report: PHASE-3 Analysis

Date: 2026-10-10. Plan: `docs/plans/PHASE-3.md`.

## Commands run
- `make check` → **151 passed**, ruff + format + mypy strict clean.
- `pytest -m integration` → **17 passed** (incl. analysis cache round-trip, full analysis flow, cache-hit-zero-calls).
- `make golden` → stub passes.

## What was built
- `app/analysis/`: bundle models + `cache_key`; stage Protocols + call-counting fakes; real signal stages
  (energy VAD w/ hysteresis, ffmpeg scene cuts, RMS energy, en/hi filler lists + boundary heuristic);
  LLM stages with strict JSON + one retry (beats, word-aligned romanization, prosody×tag emphasis);
  lazy adapters (faster-whisper/Silero/MediaPipe raise `ModelUnavailable` with pip specs).
- Cache: `analyses` table (r3) + storage JSON; second run = full hit, zero model invocations (proven by counters).
- Worker `analyze_asset` (parallel independent stages, stage progress on job row, timeline v1 auto-created
  from bundle) + `GET analysis` / `GET transcript` (`[wID] text` + beats section).
- Projects can now be created transcript-free; timeline appears after analysis (404 "analysis pending" before).

## Evidence
- VAD: 5 s tone → `[0, 4.98]` speech; 5 s digital silence → `[]`.
- Scenes on bars_5s: deterministic across runs (0 cuts on testsrc2).
- Signals on 30 s CPU: VAD 0.1 s, scenes 1.1 s, energy 0.1 s → 60 s ≈ 3 s + ASR (real whisper: ~0.1–0.3× realtime CPU).
- Beats: 20/20 schema-valid on the synthetic eval set via scripted LLM (live-provider eval needs keys).

## Deviations / gaps
- Heavy deps (torch/faster-whisper/cv2) NOT installed: no TTS/speech fixtures exist locally to verify real ASR,
  and they'd bloat CI/docker. Adapters are code-complete behind lazy imports; install on the GPU track (Phase 9).
- Real en/hi talking-head clips + hand labels still needed from the user (ASR timing gate <120 ms deferred).
- No LLM keys in this env → beats/emphasis-skipped path is default; romanization untested live (unit: 1:1 enforced).
- Faces default to empty tracks (MediaPipe in GPU image, Phase 9); reframe uses center until then.

## Verdict: GO for Phase 4.
