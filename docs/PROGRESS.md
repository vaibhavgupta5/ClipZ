# Progress

## Phase 0: Foundation (DONE — gate GO)
- Stack up: compose (pgvector pg16, redis7, Garage s3, api, worker-cpu); /readyz green.
- FastAPI health/ready, settings+logging+OTEL, SQLAlchemy 8-table model + migration r1 (upgrade/downgrade verified).
- Storage abstraction (SigV4, path-style) verified vs Garage incl. presigned round-trip.
- Provider Protocols + fakes; no-vendor-imports CI test; lavfi fixtures (5/30/89/91 s, VFR, landscape); GH Actions CI.
- Gate: docs/gates/PHASE-0.md. Gaps: real en/hi fixtures needed (user); provider choices deferred to Ph 6/7.

## Phase 1: Timeline core (DONE — gate GO)
- Frozen EDL schema, 14 pure ops, word-ID snapping, validators, versioning, LLM summary.
- 100% line coverage on app/timeline; hypothesis properties green; CRUD API with 422 actionable errors.
- Gate: docs/gates/PHASE-1.md. Gaps: transcripts client-supplied until Phase 3; asset IDs opaque until Phase 2.

## Phase 2: Ingest (DONE — gate GO)
- Presigned upload + complete + arq ingest worker + SSE progress; end-to-end verified vs compose.
- Normalization (CFR/SDR/rotation/48k stereo), 540p proxy (0.5 s GOP), 16 kHz ASR audio, waveform JSON.
- Fixture matrix green (VFR/silent/mono/91s-reject/corrupt); SSRF hardening (container allowlist + protocol whitelist).
- Gate: docs/gates/PHASE-2.md. Gaps: HDR fixture + rotation-metadata fixture need real phone clips.

## Phase 3: Analysis (DONE — gate GO)
- Bundle models/protocols/fakes; real VAD/scenes/energy/fillers; LLM beats/romanize/emphasis (strict JSON+retry).
- Lazy whisper/silero/mediapipe adapters (deps deferred to GPU track); content-hash cache (zero-call second run proven).
- analyze_asset worker + analysis/transcript endpoints; transcript-free project creation.
- Gate: docs/gates/PHASE-3.md. Gaps: real ASR untested (no speech fixtures/keys); faces empty until Phase 9.

## Phase 4: Renderer (DONE — gate GO)
- Typed filtergraph, ASS captions (EN+HI), EDL compiler, two-pass -14 LUFS audio, segment cache.
- 9 goldens (SSIM ≥ 0.98); 30 s preview 12.5 s CPU; render worker + API; injection fuzz green.
- Gate: docs/gates/PHASE-4.md. Gaps: NVENC untested; face reframe = center; animation deferred to styles.

## Phase 5: Agent core + MVP (IN PROGRESS — plan docs/plans/PHASE-5.md)
- Done: T1 reframe field + set_audio_fx op; T2 tool registry (16 tools) + pure executor
  (`execute(name, args, state)` -> ToolOutcome | ToolFailure with LLM-actionable errors;
  remove_silence via new `delete_gaps` op that never cuts a word); T3 neutral `ToolCallingLLM`
  + Claude adapter (claude-opus-5, cached prefix, streamed; ADR-008) + ScriptedToolLLM;
  T4 Director loop (8-turn cap, per-tool failure cap, ask_user stop, one preview per turn,
  literal router for undo/redo/export/preview, fenced untrusted project data).
- Also: caption size/colours + word-highlight rendering (ADR-006); renderer honours
  audio_fx denoise/loudness (ADR-007, denoise now opt-in).
- Stubbed: Gemini adapter (needs docs read + key); range preview renders the full timeline;
  Router has no small-model fallback (literal commands only).
- Next: T5 chat API + agent_runs migration + DB Workspace; T6 preview/export wiring;
  T7 30-case eval harness; T8 scripts/demo_mvp.py + gate.

## Phases 1-10: not started
