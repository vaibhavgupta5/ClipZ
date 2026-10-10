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

## Phase 5: Agent core + MVP (PARTIAL GATE — live Gemini items pending a key; docs/gates/PHASE-5.md)
- Stack pivot (owner, 2026-10-10): MongoDB (ADR-011), local ./upload ./work ./editedVideo (ADR-010),
  Gemini only via the stateless Interactions API (ADR-012), local licensed asset libraries (ADR-014).
- Built: tool registry + pure executor (16 tools); Director loop (caps, validation, one preview/turn,
  literal router, fenced untrusted data); chat SSE API over a Mongo workspace; agent_runs cost log
  (ADR-013); preview/render follow-up events; 30-case eval harness (CI scripted + `make eval-live`);
  demo script verified on the live stack up to the model-driven turns; real CPU transcription.
- Assets (`make assets`): music 40 (8 genres x 5), images 38, GIFs 7, memes 39 (personal-use only;
  monetised exports refuse them). Manifests tracked, media not.
- Stubbed / gaps: Gemini non-streamed; live evals + first-token pending a key; range preview = full;
  no SFX pack; GIF library thin; Hindi real fixture needed.
- Next: run `make eval-live` + demo with GEMINI_API_KEY, then Phase 6 (music bed + ducking using the
  local music library, attribution output, SFX).

## Phases 6-10: not started
