# Phase 6+ plan: pro editing, sound design, sponsor integrations

Target look: tech-commentary shorts (fast cuts, B-roll cutaways with slow pans, headline/stat
cards, zoom punch-ins on key words, whoosh on cuts, pops on stickers, bold captions).

## Order (each a tested commit; `make check` + `make golden` gate every step)

1. **Library breadth**: images across categories (tech, comedy, info/education, business,
   finance, fitness, food, travel, gaming, science, motivation, nature); all Imgflip top-100
   meme templates (blocklist + manual review). Same licence rules (ADR-014).
2. **Pro editing engine** (deterministic code, LLM only picks *where/what*):
   - `add_zooms`: punch-ins on chosen words (scale 1.08-1.18, ~250 ms ease), with an
     `auto` mode from emphasis/energy peaks; density cap.
   - Cut transitions (`set_transitions`): `zoom_in` | `flash` | `whip` | `none` applied as an
     *incoming* effect at the start of each clip, so segments stay independently cacheable.
   - Insert motion: Ken Burns slow push on images, spring pop-in on stickers.
   - Headline/stat cards rendered with Pillow (escaped text, safe areas).
3. **Sound design**: local CC0 SFX pack (whoosh, pop, ding, riser, boom, click, swoosh,
   record scratch) + `add_sfx` / `auto_sfx` (whoosh on cuts, pop on stickers, ding on
   stats). Renderer mixes hits with `adelay` under the voice, before loudnorm.
4. **ElevenLabs** (docs first, ADR): Sound Effects API to generate missing SFX (cached by
   prompt hash, licence from ElevenLabs terms), TTS voiceover tool, Dubbing en<->hi.
5. **Gemini critic**: sample frames + transcript of the preview -> structured critique
   (hook strength, pacing, caption legibility, insert relevance) -> Director fixes, capped
   at 2 iterations.
6. **Snowflake eval judge**: Cortex REST LLM grades live-eval transcripts; results table.
7. **DigitalOcean deploy**: droplet + compose (or App Platform spec), docs + script.
8. **Docs**: docs/JUDGES.md, docs/DEV_POST.md, docs/tiger-data-justification.pdf.

## Needs from the owner
ElevenLabs API key, Snowflake account (account id + PAT), DigitalOcean token/droplet.
