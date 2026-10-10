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

## Phase 4: Renderer (next)

## Phases 1-10: not started
