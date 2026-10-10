# PHASE-4 Implementation Plan: Renderer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Timeline → mp4, deterministic and fast, preview + final profiles.

**Architecture:** `app/render/graph.py` — typed filter nodes compiled to a single `-filter_complex` script (no external string ever concatenated; text only enters via escaped ASS files). `edl_to_ffmpeg.py` maps Timeline slices → graph; `captions_ass.py` writes per-word ASS; `audio.py` builds denoise→loudnorm chains; `segments.py` splits by clip boundaries with content-hash cache; `render.py` worker task streams `-progress` into the jobs row and stores the Render record.

**Tech Stack:** ffmpeg 9 (libass `ass` filter, `afftdn` denoise — arnndn needs an external model file, documented), NVENC-if-present else libx264, SSIM via `ssim` filter for goldens, threads=1 for deterministic goldens.

**Spec:** `docs/BLUEPRINT.md` §§7, 9; phase tasks from `prompt.md` §6 Phase 4.

## Global Constraints

- No shell; arg lists only; ASS text escaped (`\`→`\\`, `{`→`\{`, `}`→`\}`, newline→`\N`); fonts from allowlist (Liberation Sans / Noto Sans Devanagari).
- Zoom = scale+crop with `clip()`-bounded linear expressions (no zoompan jitter); rotation already baked in Phase 2.
- Denoise = `afftdn` (arnndn needs `rnnn.pb` which ffmpeg doesn't bundle — ADR in gate doc).
- Audio chain order: afftdn → loudnorm two-pass (-14 LUFS) with `aresample=async=1` guard.
- Segment cache: `segments/{slice_hash}.mp4` in object storage; concat demuxer (identical params); captions burned per segment.

## Review Focus

- ASS commas/style override parsing breaking onRTC/emoji text (escape FIRST, then format) → fuzz test.
- Zoom expression division by zero at scale=1.0 (guard: skip zoom filter when scale<=1.001) → test.
- Concat A/V drift after 10 cuts (aresample + cfr + ms-rounded boundaries) → drift test <40 ms.
- Segment cache poisoning by profile mixing (hash must include profile + slice) → key-format test.
- NVENC absent on this box (code path selected by encoder probe; CPU golden only here) → probe unit test with fake ffmpeg -h output.

---

### Task 1: Filtergraph builder

**Files:** Create `app/render/__init__.py`, `graph.py` (nodes: `Trim`, `SetPts`, `ScaleCrop`, `OverlayAt`, `FadeIO`, `ZoomPunch`, `AssBurn`, `AudioChain`, `Concat`; `compile(graph) -> (filter_script, mappings)`). Test: `tests/unit/render/test_graph.py`.

**Interfaces:** Produces node dataclasses with `render(inputs) -> (filter_line, output_label)`; `FilterGraph(inputs, nodes)` compiles deterministic labeled script; labels `[v0]`, `[a0]` allocated in order.

- [ ] Step 1: Failing tests (two trims + concat compile to exact expected script; labels deterministic).
- [ ] Step 2: Run FAIL. Step 3: Implement (pure string assembly from TYPED fields only). Step 4: PASS + check. Step 5: Commit `feat: add typed filtergraph builder`.

### Task 2: ASS captions

**Files:** Create `app/render/captions_ass.py`, `app/render/fonts.py` (allowlist + resolve). Test: `tests/unit/render/test_captions.py` (escape fuzz incl. quotes/backslash/colon/newline/emoji/RTL/Devanagari; per-word timing; safe-area positions).

**Interfaces:** `ass_for_timeline(tl, transcript_spans_out) -> str` (Dialogue events per word with `\fs` pop via `\t`, highlight color second event or `\c` override; PlayRes 1080x1920; margins keep bottom 320px + right 160px clear); `escape_ass(text)`.

- [ ] Steps 1–2: Failing tests. Step 3: Implement. Step 4: PASS + check. Step 5: Commit `feat: add ASS caption writer`.

### Task 3: EDL → ffmpeg (video)

**Files:** Create `app/render/edl_to_ffmpeg.py`. Test: `tests/unit/render/test_edl.py` (arg-list assertions: clip trims, speed setpts+atempo chain, reframe center/blur, zoom expr, overlay enable windows, NVENC selection via fake probe).

**Interfaces:** `build_video(tl, src_master, ass_path, profile) -> (args_prefix, filter_script)`; `select_video_encoder() -> h264_nvenc|libx264` (probe once, cached); profiles `preview` (540 wide, veryfast, crf 28) / `final` (1080x1920, crf 20, medium).

- [ ] Steps 1–2: Failing tests. Step 3: Implement. Step 4: PASS + check. Step 5: Commit `feat: add EDL to ffmpeg video compiler`.

### Task 4: Audio chain

**Files:** Create `app/render/audio.py`. Test: `tests/unit/render/test_audio.py` (chain order afftdn→loudnorm; atempo chaining for 0.25/4.0; two-pass loudnorm arg sets differ pass1/pass2).

**Interfaces:** `audio_chain_args(speed_spans) -> filter`; `loudnorm_pass1/2_args(...)`; `measure_loudness(path) -> {lufs, ...}` via `loudnorm=print_format=json` parse.

- [ ] Steps 1–2: Failing tests. Step 3: Implement. Step 4: PASS + check. Step 5: Commit `feat: add audio chain builder`.

### Task 5: Segments + render worker + API

**Files:** Create `app/render/segments.py` (`slice_hash`, `split_segments`), `app/workers/render.py` (register), `POST /projects/{id}/render` in `app/api/renders.py` (+ wire). Test: `tests/integration/test_render.py` (preview of bars_30s timeline: ffprobe facts; caption change re-renders 1 segment per cache stats; loudness; drift after 10 cuts).

**Interfaces:** `split_segments(tl) -> list[Timeline-slice]` (one per video clip); `render_timeline(tl, master, profile, store, on_progress) -> bytes|key`; `render_task(ctx, *, job_id, render_id)`; `POST .../render {profile, version?}` → Render row + job row + enqueue.

- [ ] Steps 1–2: Failing tests. Step 3: Implement (segments rendered to temp, cached in storage, concat demuxer, audio full-pass, mux). Step 4: PASS (30 s preview < 5 min CPU). Step 5: Commit `feat: add segment renderer, worker and API`.

### Task 6: Golden tests

**Files:** Create `tests/golden/test_render.py` + `fixtures/golden/` (generate + commit reference PNGs). Micro-benchmark recorded.

**Interfaces:** Goldens: plain cut, EN captions, HI captions (Devanagari shaping), zoom, each overlay layout (5), loudness -14±1, drift <40 ms. SSIM ≥0.98 vs stored frames; `make golden` green.

- [ ] Step 1: Render + extract frames; Step 2: eyeball 2 frames (dimensions/text presence via OCR? no — visual spot check by file size + dimensions); commit PNGs; Step 3: assert SSIM in test.
- [ ] Commit `test: add phase 4 golden renders`.

### Task 7: Security fuzz (injection)

**Files:** Test: `tests/unit/render/test_injection.py` (payloads: `"`, `\`, `:`, `\n`, `{...}`, emoji, RTL, Devanagari through overlay text/ASS/caption config → compile + render a 1 s clip, ffprobe ok). Skill table: `security-arsenal` payloads.

- [ ] Steps 1–2: Failing tests. Step 3: Fix escaping gaps. Step 4: PASS. Step 5: Commit `test: add render injection fuzz`.

### Task 8: Gate

- [ ] Preview timing recorded; `make check` + `make golden` + integration green; `docs/gates/PHASE-4.md`; PROGRESS.md; commit `docs: phase 4 gate report`.
