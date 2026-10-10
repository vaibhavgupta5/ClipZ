# Decisions (ADR style: context, decision, consequences)

## ADR-001: Build in place under `flowjob/`
- Context: The prompt pack setup says `mkdir reelforge`, but the working repo is `flowjob/`.
- Decision: Build in place. Package layout is `app/...` either way, so the folder name is cosmetic.
- Consequences: All paths in docs refer to the repo root as-is.

## ADR-002: uv-managed Python 3.12
- Context: Only Python 3.14 exists on the host; the spec pins Python 3.12 and ML wheels (torch, faster-whisper) lag on new interpreters.
- Decision: `uv python install 3.12` + `.python-version` = 3.12; `requires-python ==3.12.*`.
- Consequences: Reproducible interpreter in CI and Docker (`python:3.12-slim`).

## ADR-003: pgvector image `pgvector/pgvector:pg16`
- Context: Need Postgres + pgvector for asset embeddings (Phase 7 user-library search is removed per locked decision 3, but embeddings are still needed for CLIP ranking caches and future use).
- Decision: `pgvector/pgvector:pg16` for the `db` service.
- Consequences: `pgvector` extension available via migration (`CREATE EXTENSION IF NOT EXISTS vector`).

## ADR-005: Garage instead of MinIO for local S3-compatible storage
- Context: Blueprint says MinIO local. MinIO community edition is archived/EOL (Feb 2026); `minio/minio` is gone from Docker Hub and anonymous Quay pulls now return 401 (verified 2026-10-09; see https://www.stablebuild.com/blog/minio-pulls-from-quay-now-return-unauthorized and https://github.com/minio/minio).
- Decision: Single-node Garage v2.4.1 (`dxflrs/garage:v2.4.1`) for local dev only; docs: https://garagehq.deuxfleurs.fr/documentation/quick-start/ (retrieved 2026-10-09). One-shot `garage-init` container assigns layout, creates bucket `reelforge`, imports static dev creds (minioadmin/minioadmin). Prod still R2/S3 via the same `Storage` abstraction — no app code changes.
- Consequences: `S3_ENDPOINT` default unchanged (`http://localhost:9000` host-mapped to Garage's `:3900`); Garage is AGPLv3 — acceptable for local dev, never shipped to prod.

## ADR-004: mypy strict globally in Phase 0
- Context: Spec requires strict only on `app/timeline` and `app/tools`.
- Decision: `strict = true` for all of `app/` from the start; relax per-module only with a recorded reason.
- Consequences: More annotation work up front, fewer type surprises later.

## ADR-006: Caption size/colours on the Timeline; renderer honours caption mode and position
- Context: The MVP demo needs "yellow word-by-word captions" and "make captions bigger", but CaptionConfig had no colour or size, and the Phase 4 renderer ignored `position` and `mode`.
- Decision: Add `size_px` (40..160), `base_color`, `highlight_color` (`#RRGGBB` only) to CaptionConfig, matching BLUEPRINT §4.4. Colours are converted to ASS `&HBBGGRR&` by a strict parser, never passed through. `mode=word_highlight` emits one Dialogue event per word with the active word recoloured.
- Consequences: Additive fields with defaults keep old EDLs valid. Default timelines now render the highlight (default mode is word_highlight); goldens still pass.

## ADR-007: Renderer applies denoise/loudnorm from `audio_fx`
- Context: Phase 4 always applied `afftdn` and a fixed -14 LUFS, so the `denoise` and `normalize_loudness` tools would have had no effect.
- Decision: Denoise runs only when `audio_fx` contains a `denoise` effect. Loudness normalisation always runs; the target comes from a `loudnorm` effect (`params.lufs`, bounded -24..-8) or defaults to -14 LUFS.
- Consequences: Timelines without a denoise effect are no longer denoised by default (a behaviour change from Phase 4); the agent turns it on via the `denoise` tool.

## ADR-008: Claude Director adapter: own tool loop, claude-opus-5, cached prefix, refusal fallback
- Context: Phase 5 needs a tool-calling Director. Docs read 2026-10-10: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview, https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md, https://platform.claude.com/docs/en/build-with-claude/streaming.md (plus Anthropic's bundled Claude API reference, cached 2026-06-24).
- Decision: Providers implement a neutral one-turn `ToolCallingLLM` (app/providers/tools.py); the Director owns the loop (caps, validation, versioning), so the SDK's beta Tool Runner is not used. Claude adapter defaults: model `claude-opus-5` (`CLAUDE_DIRECTOR_MODEL`), adaptive thinking with `LLM_EFFORT=medium` for chat latency, streamed text deltas, `cache_control` on the last tool and on the system prompt (tools -> system is the stable cached prefix), `eager_input_streaming` on tools (inputs are always re-validated by the executor). On models that support it, server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`). Assistant turns are replayed with their native content (thinking blocks intact).
- Consequences: The SDK is a lazy optional import (not installed in CI); request/response mapping is unit-tested offline with a fake client. Live behaviour is untested until a key is provided (`make eval-live`). Pricing for cost logging: Opus 5 $5/$25 per MTok, Sonnet 5 $2/$10, Haiku 4.5 $1/$5; cache reads 0.1x input, cache writes 1.25x.
