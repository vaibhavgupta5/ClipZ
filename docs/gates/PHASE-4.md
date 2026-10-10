# Gate Report: PHASE-4 Renderer

Date: 2026-10-10. Plan: `docs/plans/PHASE-4.md`.

## Commands run
- `make check` → **193 passed**, ruff + format + mypy strict clean.
- `pytest -m integration` → **21 passed** (incl. 3 render + 1 hostile-caption fuzz).
- `make golden` → **9 passed** (cut, EN/HI captions, zoom, 5 overlay layouts; SSIM ≥ 0.98).

## What was built
- `app/render/`: typed filtergraph nodes; ASS writer (escaped, safe-area margins, Latin+Devanagari
  allowlist); EDL compiler (trims, speed setpts, center/blur reframe, word-anchored zooms, 5 overlay
  layouts, ASS burn); audio (afftdn → two-pass loudnorm −14 LUFS, atempo chains, aresample guard);
  content-hash segment cache + concat; full render pipeline with `-progress` streaming.
- Worker `render_task` + `POST .../render` / `GET .../renders/{id}` (Render row + job row + SSE reuse).
- NVENC selected by real-encode probe (presence alone lies without drivers); CPU fallback everywhere.

## Evidence
- ffprobe: 540×960 / 30 fps / duration ±40 ms / 1v+1a; loudness −14 ± 1 LU; A/V drift 0.0 s after cuts.
- Caption change re-renders 1/2 segments (cache stats); caption band differs 12.8% vs plain cut.
- 30 s preview on CPU: **12.5 s** (segment + 2-pass audio incl.); 60 s ≈ 25 s CPU, NVENC faster (no GPU here).
- Injection fuzz (ASS overrides, backslashes, newlines incl. CR, emoji, RTL, NUL): all render as glyphs.

## Deviations / decisions
- afftdn instead of arnndn (needs external `rnnn.pb`, not bundled with ffmpeg).
- Zoom = animated scale + static center crop (crop has no per-frame eval; constant target ⇒ exact).
- Zooms compiled post-concat (expressions need output seconds, not clip-local).
- Static overlays + fades in Phase 4; spring/pop animation is style-driven (Phase 8).
- Caption animation (`\t` pop) deferred to style presets; word-highlight color ships now.
- Silence input → anullsrc-derived audio track so every render has sound.

## Gaps / follow-ups
- NVENC path untested (no GPU); segment-parallelism across workers is Phase 9 (segments render sequentially now).
- face_track reframe falls back to center (no face data until Phase 9 GPU).
- Golden PNGs are build-specific (ffmpeg 9 + libass 0.17.5 + fonts); regenerate via GOLDEN_UPDATE=1 on toolchain change.

## Verdict: GO for Phase 5 (MVP).
