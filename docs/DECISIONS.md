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

## ADR-010: Local filesystem storage instead of S3 (supersedes ADR-005)
- Context: Owner decision 2026-10-10: no object storage; keep videos in local folders.
- Decision: `LocalStorage` routes keys by prefix: `uploads/<p>/…` -> `UPLOAD_DIR` (./upload, raw upload deleted once ingest succeeds), `projects/<p>/…` -> `WORK_DIR/<p>` (./work; master, proxy, ASR audio, waveform; deleted after a final export), content-hashed caches (`segments/`, `analysis/`) -> `WORK_DIR/cache`, `renders/<p>/…` -> `EDITED_DIR` (./editedVideo; previews and exports, kept). Uploads are multipart POSTs streamed to disk in 1 MB chunks with an on-the-fly sha256 and a `MAX_UPLOAD_MB` cap; files are served to their owner via `GET /media/<key>` (raw uploads and shared caches are never served). Keys are traversal-checked and every resolved path is re-checked to stay inside its root. Garage and boto3 removed.
- Consequences: The API now proxies upload bytes (fine for local scale; the Phase 10 "API never proxies video bytes" rule no longer applies). A project cannot be re-rendered after its final export because its working set is gone. Multi-machine deployment would need shared disk or a return to object storage behind the same `Storage` protocol.

## ADR-011: MongoDB instead of Postgres + pgvector (supersedes ADR-003)
- Context: Owner decision 2026-10-10: local MongoDB from docker.
- Decision: `mongo:8.0` in compose (host port 27018, because 27017 is often used by another local mongo), PyMongo's native `AsyncMongoClient` (one client per event loop, 5 s server-selection timeout), pydantic documents in app/models.py with UUID `_id`. Uniqueness (job idempotency keys, (timeline_id, version), analysis cache key, one timeline per project) is enforced by indexes created at API/worker startup; version allocation retries on duplicate key. SQLAlchemy, asyncpg, pgvector and Alembic removed; there are no migrations, new fields need defaults.
- Consequences: No vector search: Phase 7 ranking over a few dozen local assets runs in-process. Integration tests use a throwaway `reelforge_it_<random>` database per test.

## ADR-012: Gemini is the only LLM (supersedes ADR-008)
- Context: Owner decision 2026-10-10: remove Anthropic, use Gemini only. Docs read 2026-10-10: https://ai.google.dev/gemini-api/docs/function-calling, https://ai.google.dev/gemini-api/docs/interactions, https://ai.google.dev/api/interactions-api; request/response shapes confirmed against the installed google-genai 2.29.0 SDK types (the docs page does not specify stateless multi-turn input).
- Decision: `GeminiLLM` (app/providers/gemini.py) on the Interactions API, which Google now documents in place of the deprecated generate_content. One class serves both the Director (`complete_with_tools`) and the analysis stages (`complete`). Default model `gemini-3.8-flash` (`GEMINI_MODEL`), `thinking_level=low` (`GEMINI_THINKING_LEVEL`) for chat latency. Stateless: `store=false` and the full history is sent as typed steps (user_input / function_call / function_result / model_output), with the model's earlier steps, including thought signatures, replayed verbatim. So Google keeps no interaction state, and no `previous_interaction_id` is needed. The provider registry returns Gemini when `GEMINI_API_KEY` is set, fakes otherwise (analysis previously received the fake LLM even with a key).
- Consequences: Calls are non-streamed for now, so the first chat token arrives with the whole reply (the stream's completion event shape needs verifying against a live key). The claude adapter, ANTHROPIC_API_KEY, CLAUDE_DIRECTOR_MODEL and LLM_EFFORT are removed, as are the external provider key settings (MUSIC_API_KEY, GIF_PROVIDER_KEY, STOCK_IMAGE_KEY, MEME_PROVIDER_KEY, IMAGEGEN_KEY), replaced by local asset libraries under ASSETS_DIR.

## ADR-013: Agent run cost logging
- Context: Phase 5 requires per-run prompt hash, tools, latency, tokens and cost.
- Decision: `agent_runs` documents with a dated price table from https://ai.google.dev/gemini-api/docs/pricing (retrieved 2026-10-10; gemini-3.8-flash $0.75/$3.75/$0.075 per 1M input/output/cached until 2026-12-31, then $1.50/$7.50/$0.15). Cached input is billed at the cache rate and thought tokens at the output rate; unknown models log cost 0.
- Consequences: Assumes `total_input_tokens` includes cached tokens and `total_output_tokens` excludes thought tokens. Not yet verified against a live response; check when a key is available.

## ADR-014: Local asset libraries instead of provider APIs; popular memes as personal-use-only
- Context: Owner decisions 2026-10-10: no external music/GIF/stock/meme/image-gen APIs at runtime; build local libraries (music labelled by genre, about 30-40 GIFs, images and memes); include popular memes even though their license is unknown.
- Decision: `scripts/fetch_assets.py` (`make assets`) builds ./assets/{music,images,gifs,memes} once, each with a manifest.json recording source URL, license, license URL, attribution text and `commercial_use`. Sources: Openverse API, anonymous (https://api.openverse.org/v1/, terms https://docs.openverse.org/terms_of_service.html: respect the 20/min and 200/day limits, no catalog scraping, verify licenses, give attribution), restricted to CC0 / Public Domain Mark / CC BY (no NC because reels may be monetised, no SA/ND because we adapt and recombine). Music comes mostly from Jamendo CC BY tracks, labelled with 8 genres; GIFs are converted to silent mp4. Memes come from Imgflip's keyless `get_memes` list (the top 40 templates): license `unknown`, `commercial_use=false`. Downloads are https-only, host-allowlisted on every redirect hop, size-capped, content-type and ffprobe checked, with a title/tag blocklist on top of Openverse's `mature=false`. `app/providers/local.py` serves them (manifest entries without license fields are dropped; `asset://` URLs cannot leave ASSETS_DIR; meme captions are drawn with Pillow, never ffmpeg drawtext). Manifests are tracked in git as the license record; media files are not.
- Rule 7 amendment: assets with unknown license may reach a render only as `commercial_use=false`. `export` defaults to `monetized=true` and refuses timelines containing personal-use assets; the Director must ask the user before exporting with `monetized=false`.
- Consequences: CC BY items require credit lines in the post caption (Phase 6 attribution output). Openly licensed reaction GIFs are scarce and Wikimedia throttles anonymous bulk downloads (429), so the GIF library is smaller than 40 (see PROGRESS). There is no SFX pack yet, and AI image generation is removed. Using popular meme templates in monetised reels is the owner's legal risk; the export guard keeps it out of monetised exports by default.

## ADR-015: Noto animated emoji as the reaction-GIF library
- Context: Openly licensed reaction GIFs on Openverse/Wikimedia are scarce (7 survived review); Giphy/Tenor without their APIs would be scraping against their terms.
- Decision: Add 34 Noto Emoji Animation reaction stickers (Google, CC BY 4.0; license per https://developers.googleblog.com/updates-to-emoji-new-characters-new-animation-new-color-customization-and-more/), downloaded from the public catalog https://googlefonts.github.io/noto-emoji-animation/data/api.json and fonts.gstatic.com, kept as transparent 512 px GIFs (`alpha: true`) for pip_pop overlays. Tags combine our reaction vocabulary with the catalog tags.
- Consequences: GIF library is 41 items, all monetisable; exports need the credit line "Animated emoji: Noto Emoji Animation by Google, CC BY 4.0".

## ADR-016: Tiger Data (PostgreSQL + TimescaleDB) for all data (supersedes ADR-011)
- Context: Owner decision 2026-10-10: move everything from MongoDB to Tiger Data. The product needs relational integrity (projects own timelines, versions, renders; unique idempotency keys and version numbers) *and* high-frequency, append-only telemetry (agent runs, tool calls, pipeline stage timings, render progress ticks) that has to be rolled up for dashboards. Docs: https://github.com/timescale/docs (continuous aggregates, columnstore, retention); syntax verified against TimescaleDB 2.30.2.
- Decision: asyncpg with a typed table layer (app/db.py: insert/get/find_one/find/update, equality filters, column names validated against the models, values always bound). Schema in app/sql/*.sql, applied once each under an advisory lock. Product tables have foreign keys with ON DELETE CASCADE and the same unique constraints as before. Telemetry tables are hypertables (`tsdb.hypertable`, segmented by model/tool/stage/profile; columnstore compression after 7 days is created automatically) with hourly real-time continuous aggregates (runs, p95 latency, first token, tokens, cost, outcomes; tool error rates; per-stage p95 and real-time factor) and retention (14 days for render ticks, 180-365 days for logs). Telemetry writes are best effort and never fail a user action. `/metrics/overview` reads only the rollups; `/projects/{id}/metrics` joins a project's runs with their tool calls. Tiger Cloud is configured through `TIGER_DATABASE_URL`; the local `timescale/timescaledb:latest-pg17` container (port 5433) is for dev and tests only, and each integration test runs in its own schema.
- Consequences: MongoDB and PyMongo are removed. Hypertable primary keys include `created_at`, so telemetry rows are not referenced by foreign keys. The schema drop in test teardown unschedules TimescaleDB jobs first (they race DROP SCHEMA otherwise).

## ADR-017: ElevenLabs for generated SFX, voiceover and dubbing
- Context: Library SFX cover common hits only; creators also want narration (hook intros, recaps) and en<->hi dubs. Owner chose ElevenLabs (sponsor track).
- Docs read 2026-10-10: sound effects https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert (`POST /v1/sound-generation`, `text`, `duration_seconds` 0.5-30, `prompt_influence`, `model_id=eleven_text_to_sound_v2`, `output_format` query); speech with timestamps https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps (`audio_base64` + character alignment, `eleven_multilingual_v2`); dubbing https://elevenlabs.io/docs/api-reference/dubbing/create, https://elevenlabs.io/docs/api-reference/dubbing/get, https://elevenlabs.io/docs/api-reference/dubbing/audio/get (`status` "dubbed" when ready, `error` on failure, dub streamed as MP3/MP4). Auth header `xi-api-key`.
- Licence (https://elevenlabs.io/docs/help-center/legal/can-i-publish-the-content-i-generate-on-the-platform, https://elevenlabs.io/terms-of-use): paid plans include a commercial licence (not for Beta services); free-plan output is non-commercial and must carry "elevenlabs.io" or "11.ai" in the published title. `ELEVENLABS_PLAN` (default `free`) sets the metadata: free => `commercial_use=false` + credit line (monetised exports refuse it); paid => commercial.
- Decision: `app/providers/elevenlabs.py` (httpx, no SDK; path ids allow only `[A-Za-z0-9_-]`; responses are data, rule 6). `GeneratedAudioStore` caches output by request hash in `ASSETS_DIR/generated/` with a license-bearing manifest, so generated clips are ordinary library assets for the catalog, validator, renderer and credits. The executor stays I/O-free: the Director's async prepare step generates the clip for `generate_sfx` / `add_voiceover` and hands it to the executor in `ToolState.prepared`; any provider failure becomes `generation_unavailable` with a library fallback hint. Dubbing is a background arq job (`dub_task`) that extracts the master speech, polls, caches the dub by (speech digest, language) and commits it onto the then-current version, so edits made meanwhile survive. The renderer swaps the voice input for the dub (same timing, so cuts apply unchanged) and mixes voiceovers with the speaker ducked underneath. Without `ELEVENLABS_API_KEY` the four tools are hidden from the model.
- Consequences: Captions still follow the source transcript after a dub (they act as original-language subtitles); dubbed-language captions need ASR on the dub (gap). If Dubbing or Sound Effects is labelled Beta on the owner's plan, its output is not commercially licensed even on a paid plan: check before setting `ELEVENLABS_PLAN=paid`.

## ADR-018: Brand/tech logo tiles (Simple Icons + Lobe Icons)
- Context: Owner (2026-10-10) wants logos shown when brands or tools are named ("ChatGPT", "Claude", "Python"), the way React apps use react-icons.
- Sources:
  - Simple Icons v16, SVG data CC0-1.0, https://github.com/simple-icons/simple-icons/blob/develop/LICENSE.md. This is what react-icons ships as `si`.
  - Lobe Icons (`@lobehub/icons-static-svg`), MIT, https://github.com/lobehub/lobe-icons. Used for AI brands that Simple Icons removed at the brands' request (OpenAI/ChatGPT, Microsoft) and for AI-native colour marks (Claude, Gemini, DeepSeek...).
- `scripts/fetch_icons.py` (`make icons`) rasterises a curated set of 77 to 512 px app-icon tiles (white or dark rounded tile, brand colour). Each manifest entry records its source and licence, plus the spoken aliases the keyword engine matches ("chat gpt" -> OpenAI).
- Licence position: the SVG data is CC0/MIT, but the logos are trademarks of their owners. Showing a logo while the speaker names the product is nominative use, but brand guidelines vary. So, per "do not guess licensing", tiles are recorded `commercial_use=false` until the owner decides. Monetised exports then ask first (ADR-014 flow).
- Same rule for 7 "curated" B-roll clips in `assets/videos` (from another session), which claim CC0 with no per-file source URL: they are now `license=unverified`, `commercial_use=false`. The procedural clips (generated locally) stay CC0.

## ADR-019: Keyword-driven visuals, brief inserts, smart transitions, coverage autofix
- Keyword engine (`app/inserts/keywords.py`):
  - Takes the surviving transcript words as unigrams and bigrams. English and Hinglish stopwords are dropped, and ambiguous brand words count only when capitalised (lowercase "react" is the verb).
  - Matches every library: exact alias = 10; otherwise every spoken token must be a tag, scored 2..5 by normalised IDF, with title-only matches at 0.6x and generic medium tags ignored. The threshold is 3.0.
  - Keeps the top 3 alternatives per term, so a repeated word gets the next unused asset.
  - Shown to the model as `<visual_candidates>` (fenced data). Used by the new `auto_visuals` tool and by `add_inserts` items without a query.
- Durations are capped per kind: icon 1.5 s, gif 1.6 s, image 2.2 s, meme 2.5 s, video 3.5 s. Split screen no longer stretches inserts to fill gaps (this caused 6-15 s images). Panel visuals start as the word is said and hand over back to back.
- One overlay-window function (`snap.overlay_window`) serves both the executor and the renderer; they previously disagreed for `split_top`.
- Transitions apply only at real cuts (`snap.transition_points`): the cut must skip at least 0.6 s of source, come at least 3 s after the previous transition, and not sit under a full-screen insert. `auto_sfx` whooshes only at real cuts (at most every 1.5 s), never stacks hits within 0.35 s, and accents keywords when there is nothing to sync to.
- Coverage autofix: if requested music, SFX, captions, noise reduction, 9:16 framing or silence removal are still missing after the one follow-up note, the Director applies defaults itself. Music genre comes from the request mood, then the transcript topic, limited to installed genres. The reply lists them under "Applied".
- The Director prompt is now `app/agent/prompts/director.txt`, role-based: role, inputs, process (understand -> plan -> edit -> verify), craft rules, safety. The new read-only `plan_edit` tool makes the plan visible.
- Deviation from ADR-010 (from the integrated work-in-progress, kept): `./work/<project>` is no longer deleted after a final export. Re-exports after late edits (and dubbing) need the master. Cleanup should move to project deletion (gap).
