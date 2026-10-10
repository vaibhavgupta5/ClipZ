# Agentic Reel Editor: Backend Blueprint (v2)

Python + FFmpeg backend. An LLM agent edits reels from natural-language chat. Models: Claude / GPT (planning + tool calling), Gemini (video understanding). React Native app comes later, so everything here is API-first.

---

## 0. What changed in v2

1. **Auto Insert Engine**: "add memes / images" and the system finds the best moments, picks assets, places and animates them.
2. **Style Engine**: reference reels (e.g. a Varun Mayya-type tech/business explainer style) are analysed into a reusable **StyleProfile** JSON. The agent edits *to a style* instead of from vibes.
3. **Trend layer**: trends are shipped as hot-updatable data packs, not code.
4. **Director → Executor → Critic** agent loop for higher quality.

---

## 1. Core principle

**The LLM never writes raw FFmpeg.** It edits a structured timeline (JSON EDL) through typed tools. A deterministic renderer compiles the timeline into FFmpeg.

```
User chat ─▶ Director (LLM + tools) ─▶ Timeline JSON (EDL) ─▶ Renderer ─▶ FFmpeg ─▶ mp4
                 ▲        │                    │
     Analysis cache   StyleProfile        Low-res preview ─▶ Critic (Gemini) ─┐
                 ▲                                                            │
                 └────────────────── fix loop ◀───────────────────────────────┘
```

Benefits: validated ops, undo/redo via timeline versions, diff-only re-renders, and the same timeline JSON can be rendered by the React Native app later.

---

## 2. Feature map

### 2.1 Analysis (auto on upload, cached by content hash)
| Signal | Tool |
|---|---|
| Word-level transcript | faster-whisper, or WhisperX for forced alignment |
| Speech / silence map | Silero VAD |
| Shots / scenes | PySceneDetect |
| Faces, speaker tracks | MediaPipe or YOLO-face, pyannote for diarization |
| Visual understanding | Gemini video input ("funniest moment", "what is on screen at 0:12") |
| Loudness / energy curve | ffmpeg `ebur128`, librosa |
| Filler words | derived from transcript ("um", "uh", "like", "matlab", "basically") |
| Emphasis words | LLM tags + prosody (pitch/energy peaks on the word) |
| Language / code-mix | Whisper language ID. Handle Hinglish explicitly (see pitfalls). |
| **Semantic beats (new)** | LLM pass over transcript: punchlines, claims, numbers, named entities, topic shifts, emotional peaks |

### 2.2 Editing tools (agent-callable)
- **Cutting:** `trim`, `split`, `delete_range`, `reorder`, `remove_silence`, `remove_fillers`, `cut_by_transcript(start_word, end_word)`, `auto_highlight(duration)`
- **Time:** `speed_ramp`, `freeze_frame`
- **Framing:** `reframe_9x16(face_track | center | blur_bg)`, `zoom_punch_in(word_id, scale)`, `stabilize`
- **Captions:** `add_captions(style, position, highlight_mode)`, `translate_captions`, `emphasize_words`
- **Audio:** `denoise`, `normalize_loudness(-14 LUFS)`, `ducking`, `add_music`, `separate_vocals` (Demucs), `add_sfx(type, at)`
- **Visual:** `color_grade(LUT)`, `bg_remove` (RVM / SAM2), `transitions`, `overlay_text`, `logo`, `progress_bar`
- **Inserts (new):** `suggest_inserts`, `insert_asset`, `swap_asset`, `remove_insert`, `set_insert_density`
- **Style (new):** `apply_style(profile_id)`, `extract_style(reference_asset)`, `style_diff(version_a, version_b)`
- **Meta:** `get_timeline`, `undo`, `preview(range)`, `ask_user`, `compare_versions`

---

## 3. NEW: Auto Insert Engine (memes, images, GIFs, reaction clips)

User says *"add memes"*, *"put relevant images"*, *"make it funnier"*. The engine decides **where**, **what**, **how**.

### 3.1 Pipeline

```
Transcript + beats ─▶ 1. Moment detection ─▶ 2. Query generation ─▶ 3. Candidate sourcing
                                                                          │
   Timeline ◀── 6. Animate + render ◀── 5. Placement ◀── 4. Rank + verify ◀┘
```

**1. Moment detection (LLM, text only, cheap and fast).** Output a list of beats with `word_id` range, `intent`, and `query_hint`:

```json
{"beat_id":"b7","words":["w112","w118"],"intent":"illustrate",
 "text":"OpenAI burned through billions","query_hint":"money burning, cash on fire"}
```
Intents: `illustrate` (show the thing), `react` (reaction meme to a claim), `joke` (punchline meme), `emphasize` (stat/number card), `proof` (screenshot / headline card), `transition`.

**2. Query generation.** One short search query plus 2 backups per beat. Include a "tone" tag (sarcastic, shocked, celebratory).

**3. Candidate sourcing (in parallel, per beat).**
| Source | Use for | Notes |
|---|---|---|
| User library (pgvector + CLIP/SigLIP embeddings) | their own memes, brand assets | Highest priority. Embed on upload. |
| Giphy / Tenor APIs | reaction GIFs and stickers | Check the API terms and attribution rules. Convert GIF to mp4/webm on ingest. |
| Pexels / Unsplash / Pixabay | clean stock photos and clips | Permissive licenses. Best for `illustrate`. |
| Imgflip API | classic meme templates + auto top/bottom text | Good for `joke`. |
| AI image gen (Gemini image, Flux, etc.) | exact custom visuals | Slower and costs money. Use as a fallback when nothing ranks well. |
| Headline / tweet / stat card renderer | `proof`, `emphasize` | Render your own card from HTML (Playwright) or Skia. No scraping of copyrighted screenshots needed. |
| Sound-effect library (Freesound, own pack) | whoosh, pop, vine-boom-type hits | Pair every visual insert with a tiny SFX. |

**Licensing reality check:** random web memes are usually copyrighted and the reel may be monetised. Default to licensed APIs, user uploads, and generated or rendered assets. Make "user-supplied only" a switch. Store the license/source per asset for later audits.

**4. Rank + verify.**
1. CLIP/SigLIP similarity between the query and each candidate (fast, local or GPU).
2. Gemini vision rerank on the top 5 (is it relevant, is it readable at phone size, is it safe).
3. Filters: NSFW/safety classifier, resolution floor, watermark detector, dedupe against already-used assets, tone match.
4. Return **top 3 per slot**. Auto mode picks #1. Interactive mode shows a swipe carousel in the app.

**5. Placement rules (deterministic code, not the LLM).**
- Anchor at the **end of the beat's last word + 0 to 150 ms**, so the visual lands as the line lands (punchline timing).
- Duration: reaction/meme 0.7 to 1.5 s, illustrate 1.2 to 2.5 s, stat card 1.5 to 2.5 s.
- **Density cap** (style-controlled): about 1 insert per 3 to 6 s, minimum gap 2 s, never two fullscreen inserts back to back.
- Prefer natural pauses or sentence ends. Don't cover the speaker's face during emotional peaks unless the style is cutaway-heavy.
- Keep speaker **audio continuous** under every insert (J-cut / L-cut feel).
- Respect **safe areas**: keep key content in the central region. Captions, buttons and UI overlap the bottom and right edges on Reels.
- Don't place the first insert too early if the hook line needs the face. Hook-first styles can override this.

**6. Layout modes and animation.**
| Mode | Look |
|---|---|
| `cutaway_full` | fullscreen image/clip for 1 to 2 s, speaker audio continues |
| `pip_pop` | sticker pops up in a corner or centre, spring scale-in, optional wiggle |
| `split_top` | meme top half, speaker bottom half (reaction format) |
| `lower_third_card` | headline or stat card |
| `blur_bg_contain` | non-9:16 images fit inside a blurred copy of themselves (never stretch) |

Animation sketch (validate on your FFmpeg build). Static fullscreen cutaway with continuous speaker audio:
```bash
ffmpeg -i main.mp4 -loop 1 -t 1.4 -i meme.png -filter_complex "
 [1:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,
      format=rgba,setpts=PTS+12.4/TB[m];
 [0:v][m]overlay=0:0:enable='between(t,12.4,13.8)'[v]" -map "[v]" -map 0:a out.mp4
```
For pop-in/spring/shake animation, either use `scale` with `eval=frame` expressions or pre-render the sticker as a short alpha webm/mov (Skia / Pillow frames piped to ffmpeg) and overlay that. Pre-rendered animated stickers are easier to tune and reusable across projects.

### 3.2 Tool API
```python
suggest_inserts(
    density: Literal["low","medium","high"] = "medium",
    kinds: list[Literal["meme","image","gif","stat_card","headline","reaction"]] = ["meme","image"],
    sources: list[str] = ["user_library","giphy","pexels","generated"],
) -> list[InsertSuggestion]   # beat, 3 candidates each, proposed placement

insert_asset(beat_id: str | None, at_word: str | None, asset_id: str,
             layout: str = "cutaway_full", duration_ms: int | None = None, sfx: str | None = None)
swap_asset(insert_id: str, asset_id: str)
```

### 3.3 Timeline representation
```json
"overlays": [
  {"id":"ins1","type":"image","asset":"giphy:abc123","anchor_word":"w118",
   "offset_ms":80,"duration_ms":1100,"layout":"pip_pop",
   "anim":{"in":"spring_pop","out":"fade_fast"},
   "sfx":{"asset":"sfx:pop_03","gain_db":-8},
   "license":{"source":"giphy","attribution":null}}
]
```
Anchoring to **word IDs** (not seconds) means inserts follow the speech when the user later deletes a silence or re-cuts the video.

### 3.4 Making it fast
- Run moment detection **while the transcript streams in**. Start candidate fetching before the user finishes typing "add memes".
- Pre-embed the user's library at upload time. Vector search is then milliseconds.
- Convert GIFs to mp4/webm on first use and cache them in object storage.
- Prefetch top-3 per slot to local disk on the render worker, so rendering never waits on third-party APIs.
- Tool latency budgets: LLM beat pass under 3 s, sourcing under 2 s (parallel), rerank under 2 s.

---

## 4. NEW: Style Engine (creator-level edits)

### 4.1 The idea
Don't ask the LLM to "edit like Varun Mayya". Convert a style into **measurable parameters**, store them as a `StyleProfile`, and make the agent and renderer obey the profile.

### 4.2 What I could and couldn't verify
Public, detailed breakdowns of Varun Mayya's editing were thin in my search. What turned up was mostly freelancer briefs describing his look as fast-paced, audio-reactive, with snappy cuts, sound-synced visuals, bold on-screen elements and a polished colour grade. So treat the "Varun-like" preset below as a **hypothesis built from general observation** of tech/business explainer reels. Validate and tune it with the extraction pipeline (4.3) on reels you have the rights to analyse.

Other creator styles have public breakdowns you can use as sanity checks. One style-analysis site describes Hormozi-type edits as yellow word-by-word captions with jump cuts every 1 to 3 s and punchy zooms, Ali Abdaal-type edits as selective colour pop with handwritten-style captions, and Tom Noske-type edits as bold 3D-shadow captions with cinematic B-roll intros. These make good starter presets.

### 4.3 Style extraction pipeline (`extract_style`)
User uploads 1 to 5 reference reels they own, have permission to use, or have downloaded legitimately for private analysis. **Do not build an Instagram scraper**: it breaks platform terms and is brittle.

| Dimension | How to measure |
|---|---|
| Pacing: shot length, cuts per minute, jump-cut rate | PySceneDetect plus transcript-gap cuts (cuts inside continuous speech) |
| Speech rate, pause tightness | Whisper words per second, VAD gaps |
| Caption look: font family guess, size, colours, stroke/shadow, position, words per line, animation | Sample frames, OCR (PaddleOCR) for boxes and text, Gemini for font/colour/animation description, pixel sampling for exact colours |
| Keyword highlighting rule | Diff colour of words within a caption vs transcript emphasis |
| Zoom punch-ins: frequency, scale, duration, trigger | Frame-to-frame scale estimation (feature matching / optical flow) around cuts and emphasis words |
| Inserts: density, kind, duration, layout | Gemini video pass returns insert events (timestamp, type, layout) and code aggregates them |
| SFX: type, density, sync-to-cut | Onset detection plus audio tagging (YAMNet / CLAP), sync offset vs cut times |
| Music bed: presence, loudness under voice | Demucs split, LUFS difference |
| Colour grade | Histogram and average LUT estimate, optionally pick the nearest from your LUT pack |
| Hook structure (first 3 s) | Gemini: hook type (bold claim, question, visual shock), text-on-screen yes/no, first-cut time |
| Aspect / safe areas | Measured overlay positions |

Rule of thumb: **numbers from code, labels from Gemini.** Gemini is great at "what is this caption animation" but unreliable at exact timings and counts.

### 4.4 StyleProfile schema (example: hypothesis, tune from data)
```json
{
  "id": "tech_explainer_fast_v1",
  "pacing": {"avg_shot_len_s": [1.2, 2.5], "jump_cut_in_speech": true,
             "silence_max_ms": 180, "speed": 1.05},
  "hook": {"max_first_cut_s": 1.5, "text_on_screen": true, "pattern": "bold_claim_then_context"},
  "captions": {
    "mode": "word_by_word", "words_per_line": [1, 3], "case": "upper",
    "font": "Montserrat-ExtraBold", "size_px": 84, "position": "center_lower",
    "colors": {"base": "#FFFFFF", "highlight": "#FFD400"},
    "stroke_px": 6, "animation": "pop_scale_110", "emoji": "sparse"
  },
  "zoom": {"on_emphasis": true, "scale": [1.08, 1.18], "dur_ms": 220, "alternate_with_wide": true},
  "inserts": {"density": "medium_high", "kinds": ["image","screenshot_card","reaction_gif","meme"],
              "layout_mix": {"cutaway_full": 0.4, "pip_pop": 0.4, "stat_card": 0.2},
              "dur_ms": [800, 1800], "sfx_per_insert": true},
  "sfx": {"on_cut": "whoosh_soft", "on_insert": "pop", "gain_db": -10, "density": "high"},
  "music": {"bed": "low_energy_beat", "duck_under_voice_db": -14, "lufs_target": -14},
  "color": {"lut": "punchy_warm", "contrast": 1.08, "saturation": 1.1},
  "language": {"caption_script": "latin", "code_mix": "hinglish"}
}
```
Ship a library of presets (`hormozi_like`, `abdaal_like`, `minimal_clean`, `meme_heavy`, `cinematic_vlog`, `tech_explainer_fast`) and let users save their own from `extract_style`.

### 4.5 How the agent uses the profile
- The profile is injected into the **Director** prompt (small JSON, cacheable).
- Each tool reads defaults from the profile, so "make it look like my reference" is just `apply_style`.
- The user can override in chat: "same style but fewer zooms", which patches the profile for this project.
- **Critic** (Gemini watching the preview) scores the render against the profile checklist: caption position, hook timing, insert density, silence gaps. It returns concrete fixes ("first cut at 2.4 s, target 1.5 s") that go back to the Director.

---

## 5. NEW: Trend layer

### 5.1 What the current guidance says
Recent 2026 creator guides agree on a few things: watch time and completion are the dominant signals, hooks decide distribution within roughly the first three seconds, captions help reach and accessibility, dead air hurts pacing, and trends work better as packaging than as the substance of a reel. Also, Instagram's own guidance warns that captions, buttons and controls cover the bottom and right edges, so keep key text central.

Implication: your engine's priority order should be **hook → retention pacing → readable captions → trends**.

### 5.2 Where trend data comes from
There is **no official public API for trending Reels or trending audio**. Options, from safest to riskiest:

1. **Curated trend packs (recommended).** A human or team reviews trending formats weekly and publishes a JSON pack (caption style tweaks, transition type, SFX, meme templates, structure like "numbered listicle"). The backend hot-loads packs without a deploy.
2. **User reference reels** run through `extract_style` (section 4.3).
3. **Licensed third-party trend data** if a vendor offers it with clear terms.
4. **Scrapers (avoid).** Fragile, against platform terms, and a business risk.

Trending *audio* has licensing nuance: audio from the Instagram library is licensed for use inside the app. If your backend burns music into the file, use royalty-free or licensed music. Option: export without music and let the user add the trending sound inside Instagram at posting time. Offer both.

### 5.3 Trend pack example
```json
{
  "pack": "weekly_2026_w41",
  "formats": [{"id":"numbered_list","overlay":"progress_counter","hook":"number_first"}],
  "caption_tweaks": {"highlight_color":"#00E5FF"},
  "transitions": ["whip_pan","zoom_blur"],
  "sfx": ["riser_short","whoosh_soft"],
  "meme_templates": ["imgflip:181913649"],
  "expires": "2026-10-24"
}
```
Expiry dates matter: stale trends look worse than no trend.

---

## 6. Agent design (Director → Executor → Critic)

| Role | Model | Job |
|---|---|---|
| Router | tiny model (Haiku / Flash) | Classify intent. Simple commands ("make captions yellow") go straight to a tool call. |
| Director | Claude or GPT | Plan edits from transcript + StyleProfile + current timeline. Tool calling. |
| Vision | Gemini | Video/frame understanding: moment finding, insert verification, extract_style labels. |
| Executor | plain Python | Validates, snaps, applies tool calls. No LLM. |
| Critic | Gemini (frames or short clip) | Compares the preview with the style checklist and the user's request, returns structured fixes. |

Rules that keep it reliable:
- Context for the Director: transcript with word IDs (`[w12] hello [w13] guys`), scene list, silence ranges, beats, current timeline, StyleProfile. **Never send raw video to the planner.**
- Reference **word IDs and beat IDs, never timestamps.** Code snaps to real boundaries and adds 80 to 150 ms padding.
- Tool errors return clear validation messages so the model self-corrects.
- Cap the loop at 2 critic iterations in interactive mode. Quality mode can go to 4.
- Ambiguity ("make it better"): offer 2 to 3 variants or ask one question.
- **MCP:** expose the tools as an MCP server for Claude Desktop, Cursor, etc. Internally use direct in-process calls (faster). Use MCP clients for external services (stock footage, music, Drive).
- Prompt-cache the system prompt, tool schemas and StyleProfile.

---

## 7. Making it fast

1. **Proxy workflow.** On upload, transcode a 480p/720p proxy with a short GOP (keyframe every 0.5 s), constant FPS, SDR. Analyse and preview from the proxy. Render full-res only on export.
2. **Pre-compute while the user types.** Transcription, VAD, scenes, beats and library embeddings all start at upload completion.
3. **Cache by content hash:** transcripts, scenes, face tracks, embeddings, converted GIFs, candidate lists.
4. **Single-pass `filter_complex`.** No intermediate files.
5. **Stream copy (`-c copy`)** for simple keyframe cuts. Re-encode only where needed.
6. **GPU:** NVDEC/NVENC for decode/encode. faster-whisper on GPU runs far faster than realtime.
7. **Segment-parallel rendering.** Split the timeline into chunks by timeline hash, render in parallel, concat. Only re-render changed segments.
8. **Captions via ASS/libass** (one pass, cheap). Use Skia/Remotion-style rendering only for the fanciest animated captions or stickers.
9. **LLM latency:** stream, prompt-cache, small model for easy intents, parallel tool calls.
10. **Progress events** over WebSocket/SSE ("transcribing 40%", "finding memes", "rendering preview") so it feels instant.
11. **Preview-first:** a 540p preview renders in seconds. Final 1080p renders in the background.

Caption animation sketch with ASS (per-word events with a scale pop):
```
Dialogue: 0,0:00:01.20,0:00:01.55,Pop,,0,0,0,,{\an5\fscx85\fscy85\t(0,90,\fscx110\fscy110)\t(90,160,\fscx100\fscy100)\c&H00D4FF&}BILLIONS
```

---

## 8. Infra recommendation

| Layer | Pick |
|---|---|
| API | FastAPI (async) with WebSocket/SSE for chat and progress |
| Queue | Arq or Celery + Redis. Temporal later for durable multi-step workflows. |
| DB | Postgres + **pgvector** (projects, timelines, versions, chat, asset embeddings) |
| Object storage | Cloudflare R2 or S3 (R2 has no egress fees, which matters for video) |
| Uploads | Presigned multipart or tus (resumable, essential on mobile networks) |
| CPU workers | Containers with FFmpeg: cuts, captions, audio, overlays |
| GPU workers | Modal (easiest, scale-to-zero), RunPod, or Lambda Labs for Whisper, Demucs, segmentation, CLIP, NVENC |
| Delivery | CDN, short mp4 previews or HLS |
| Asset cache | Redis + local NVMe on render workers for prefetched inserts |
| Observability | Sentry, OpenTelemetry, per-job FFmpeg stderr logs, per-tool latency metrics |
| Cost control | Per-user quotas, 60 to 90 s length cap, aggressive caching, cheap-model routing, AI image gen as last-resort fallback |

Suggested repo layout:
```
app/
  api/            # FastAPI routes, WS
  agent/          # director.py, critic.py, router.py, prompts/
  tools/          # one file per tool, typed with pydantic
  timeline/       # schema.py, ops.py, snap.py, versions.py
  render/         # edl_to_ffmpeg.py, captions_ass.py, overlays.py, segments.py
  analysis/       # asr.py, vad.py, scenes.py, faces.py, beats.py
  inserts/        # moments.py, sources/{giphy,pexels,imgflip,generate}.py, rank.py, place.py
  style/          # extract.py, profiles/*.json, apply.py
  trends/         # packs/*.json, loader.py
  workers/        # arq tasks (cpu, gpu)
  mcp_server/     # exposes tools via MCP
```

---

## 9. Data model

```
Project ─┬─ Assets (raw video, music, b-roll, memes, each with license + embedding)
         ├─ Timeline versions (v1, v2, ... each an EDL JSON + style_id + trend_pack)
         ├─ Messages (chat history + tool calls)
         ├─ Inserts (beat, candidates, chosen, license)       ← new
         └─ Renders (preview/final, linked to timeline hash)
User ── StyleProfiles (saved), Brand kits, Asset library
```

Timeline example:
```json
{
  "style": "tech_explainer_fast_v1",
  "tracks": {
    "video": [{"asset":"a1","src_in":0.0,"src_out":4.2},
              {"asset":"a1","src_in":9.1,"src_out":15.0}],
    "audio": [{"type":"denoise"},{"type":"loudnorm","lufs":-14}],
    "captions": {"style":"from_profile","mode":"word_highlight"},
    "zooms": [{"anchor_word":"w44","scale":1.14,"dur_ms":220}],
    "overlays": [{"id":"ins1","type":"image","anchor_word":"w118","layout":"pip_pop"}]
  },
  "output": {"aspect":"9:16","res":"1080x1920","fps":30}
}
```

---

## 10. Pitfalls to handle early

- **iPhone footage:** VFR, rotation metadata, HDR (HLG/Dolby Vision). Normalize to CFR + SDR in the proxy step.
- **A/V drift after cuts:** use `aresample=async=1` and accurate boundaries.
- **Mid-word cuts:** snap to word boundaries with 80 to 150 ms padding.
- **Caption sync:** WhisperX alignment, 1 to 3 words per line for reel styles.
- **Hinglish / code-mixed speech:** Whisper often mixes scripts. Decide the caption script (Latin vs Devanagari) in the style profile, and test your ASR on real Hinglish early. Use fonts with full glyph coverage.
- **Insert relevance:** a wrong meme is worse than no meme. Keep a confidence threshold. Below it, skip the slot.
- **Over-inserting:** enforce density caps and minimum gaps in code.
- **Safe areas:** keep text and key inserts out of the bottom strip and right rail.
- **Copyright:** license metadata per asset, user-supplied-only switch, no scraping of Instagram or random meme sites.
- **Prompt injection:** transcripts, OCR text and web results are untrusted data. Never let text inside a video or an asset page change tool permissions.
- **Safety:** sandbox FFmpeg, never accept raw filter strings from users or the LLM, cap file size and duration.
- **Long uploads on flaky networks:** resumable uploads.

---

## 11. Roadmap

**MVP (2 to 3 weeks)**
Upload, proxy, Whisper transcript, chat agent with `remove_silence`, `trim`, `add_captions` (ASS, word highlight), `denoise`, `reframe_9x16`, export. One FFmpeg render, undo, WebSocket progress.

**v2 (weeks 4 to 7)**
- Style presets (3 to 4 hand-tuned) and `apply_style`
- Filler removal, music + ducking, zoom punch-ins, SFX on cuts
- **Insert Engine v1:** beats → stock images + Giphy/Tenor + user library → auto placement with `cutaway_full` and `pip_pop`
- Face-tracked reframing

**v3 (weeks 8 to 12)**
- **`extract_style` from reference reels**, user-saved styles
- Critic loop with Gemini
- Segment-parallel rendering, variants ("give me 3 versions")
- Stat/headline card renderer, Imgflip meme templates, AI-generated inserts
- Weekly trend packs
- Brand kits, multilingual captions, dubbing, MCP server publishing

---

## 12. Open questions to decide before building

1. Max reel length and expected volume (drives GPU budget). - 90secs
2. Languages at launch (Hinglish, Hindi, English, Telugu?). - english/hindi
3. Will users upload their own meme library, or must the system source everything? - no
4. Do you burn in music, or export music-free for the user to add trending audio in-app? - no, use some external free api
5. Auto mode (agent decides everything) vs approval mode (user picks from candidates)? Probably both. 

---

## References (search results used for the trend notes)
- Klap: Instagram Reels Optimization 2026 playbook, and 10 Reels trends for 2026 (klap.app/blog)
- Captions.ai: how to make Instagram Reels (2026 guide)
- Opus: Reels caption and subtitle best practices
- quso.ai: Reels best practices 2026
- SocialKit: Instagram Reels guide (specs, safe areas)
- invideo: Reels guide (hooks, first three seconds)
- Choppity: re-create popular video editing styles (creator style breakdowns)
- Freelancer.com briefs mentioning a Varun Mayya-type editing style (informal, low-confidence)