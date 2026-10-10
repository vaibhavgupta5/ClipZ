# Phase 6c plan: learn the user's editing style; adaptive / split / blur fixes

Owner request (2026-10-11): learn the user's editing patterns and add a studio button that drafts
a Director prompt in that style, which the user reviews, edits and sends. Also check that the
Director, adaptive framing, split screen and the Gaussian-blur view are correct.

## Review findings (adaptive / split / blur / Director)
1. **Transitions at every breath trim (regression from 961d384).** The segment renderer styles
   the first slice of every clip (`is_speech_cut`), not just real cuts. ADR-019 says transitions
   apply only at real cuts (`snap.transition_points`). Repro: 9 clips, 0 real cuts, 8 styled.
2. **Adaptive inserts never open the split view unless the model passes `layout="split_top"`.**
   `_is_split` only knows `split`/`split_bottom`, so in adaptive mode `auto_visuals` and
   `add_inserts` without a layout place `pip_pop` stickers / full-screen inserts and the reel
   stays in the blur view for its whole length.
3. Adaptive segments decide their reframe twice (segment `is_split_layout` for the cache key,
   "any split_top overlay in the slice" for the pixels). Pass the segment's reframe explicitly so
   the two can never disagree.
4. Meme caption-hiding windows use the word end, but split-top memes start at the word start (and
   adaptive chains them). Use the same window the renderer draws.
5. `make check` is red: 20 ruff errors and 4 unformatted files from 961d384.

Checked and correct: split-interval smoothing (6 s hold, bridging, intro/outro), chained top-panel
windows (contiguous, never empty), blur sections moving captions below the video, filter label
reuse across overlays (FFmpeg pairs consumed labels), Director loop / coverage / consent guard.

## Steps
1. Fix 1-5 with unit tests (real-cut segments, adaptive default panel layout, explicit segment
   reframe, meme windows).
2. `app/style/learn.py` (pure, no I/O): `StyleSample` (accepted timeline, exported?, tools run,
   music genre) -> `StyleProfile` of habits (area, label, instruction, share) -> prompt text.
   Weighted majority / median over the user's other edited reels (exported reels weigh 2x); an
   optional element joins the style only when used in >= half of the weighted reels. Counts
   (visuals, zooms) are scaled to the target video's length. Transcript text never enters it.
3. `GET /projects/{id}/style-prompt` (`app/api/style.py`): gathers samples (other projects of the
   same user with at least one kept edit, newest 20), returns habits + prompt + a note.
4. Studio: "My style" button in the composer -> review card (habit chips with shares, editable
   prompt, Send to Director / Discard).
5. Tests: pure learning + prompt, page wiring; opt-in integration test for the endpoint.
   ADR-021, PROGRESS.
