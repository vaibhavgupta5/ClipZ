# Phase 6b plan: smarter Director, relevant visuals, brand icons, web app

Owner feedback (2026-10-10): the Director should pick keywords; images stay on screen too long;
visuals must match what is said; transitions should make sense; SFX + music were not both applied
when asked; the prompt should be a readable role-based text file that understands the subtitles and
plans first; brand icons (ChatGPT, Claude...) as visuals; a Claude-Code-like web page to upload,
brainstorm, chat, watch and download.

## Steps (each a tested commit, `make check` green)
1. **Baseline**: make the split-screen / B-roll / headline-card / critic work-in-progress pass
   `make check`. Remove the "stretch inserts to fill gaps" rule (cause of 6-15 s images).
2. **Keyword engine** (`app/inserts/keywords.py`): content keywords and brand names from the
   surviving transcript words (stopwords en + Hinglish, phrases, numbers), matched against every
   library with IDF-weighted tag scoring + exact alias bonus + a minimum score (no weak matches).
   Output: visual cues `(word, phrase, kind, asset, score)`.
   - Shown to the model as `<visual_candidates>` data.
   - `add_inserts` items may omit `query` (use the cue for that word).
   - New tool `auto_visuals` places the best cues deterministically.
3. **Durations**:
   - Per-kind caps: icon 1.4 s, gif 1.6 s, image 2.2 s, meme 2.5 s, video 3.5 s.
   - Split mode chains short visuals back to back instead of stretching them. Gaps get more cues;
     if none remain, the top panel shows the brand canvas.
4. **Transitions that make sense**: a style is applied only at real cuts. A cut qualifies when it
   skips at least 0.6 s of source, comes at least 3 s after the previous transition, and is not
   hidden under a full-screen insert. Micro silence trims stay hard cuts, as editors do.
5. **Coverage autofix**:
   - Music, SFX, captions, noise reduction and 9:16 framing are applied deterministically, with
     defaults, when the model still skips them after the follow-up note.
   - The music genre is inferred from the request and transcript.
6. **Brand icons**: Simple Icons (CC0 SVG data, the same set react-icons ships as `si`), curated
   tech list, rasterised to app-icon tiles. Trademarks belong to their owners, so tiles are marked
   `commercial_use=false` until the owner decides (ADR-018).
7. **Role-based prompt**: `app/agent/prompts/director.txt`.
   - Sections: role, what you are given, process (understand -> plan -> edit -> verify), craft
     rules, safety.
   - New `plan_edit` tool (read-only, validated word ids) so the plan is visible.
8. **Web app** (`/app`, single static page, no build step):
   - Projects + upload, Brainstorm mode (no edits; ends with a suggested final prompt), Edit mode
     (streams Director events, tool calls as steps).
   - Results panel with player and download, plus transcript and plan view.
