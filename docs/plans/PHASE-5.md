# PHASE-5 Implementation Plan: Agent Core + MVP

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chat-driven editing end to end (MVP milestone).

**Architecture:** `app/agent/` holds Router (keyword fast-path + LLM fallback), Director (tool loop with caps), Executor (tool calls → timeline ops/versioning/renders), context builder. `app/tools/` holds one pydantic in/out module per MVP tool + registry exporting Claude/Gemini/MCP schemas from the same source. Providers gain `complete_with_tools` (new `ToolCallingLLM` protocol; Claude/Gemini lazy adapters). Chat API streams SSE events; agent_runs table logs every run. Eval harness (30 machinery cases) runs on scripted LLMs in CI.

**Tech Stack:** pydantic tool schemas, SSE (existing), arq render enqueue (existing), anthropic/google-genai SDKs lazy (not installed — no keys in this env).

## Global Constraints

- Tools edit Timelines only through Phase-1 ops (no new mutation paths); every tool call validated before version creation.
- Tool loop caps: 8 tool iterations, 2 critic-less retries per call (critic is Phase 9).
- Prompt-injection rule: transcript/beats text is DATA; tool permission never derives from media text (test: "ignore previous instructions…" triggers zero tools).
- New Timeline field `reframe` (center|face|blur, default center) + `set_audio_fx` op — additive, defaults keep old EDLs valid.
- `make check` stays fast: no vendor SDK installs; scripted LLMs in CI; live evals opt-in.

## Review Focus

- Tool-call JSON with wrong arg types (model sends strings for ints) → pydantic coerces or returns actionable error? strict-ish: coerce scalars, reject shapes.
- remove_silence deleting speech on VAD flicker (min_silence floor 300 ms + hangover already in VAD) → test on silent_5s bundle (all silence → words all cut? words none... silent bundle has no words; assert no-op + message).
- Preview render per chat turn exploding queue (preview only after VISUAL/audio edits, debounced by version change) → test: no-op chat triggers zero renders.
- SSE stream never closing on director stall (iteration cap guarantees termination) → cap test.
- agent_runs cost math drifting from provider pricing (cost table in code with per-model rates, tested).

---

### Task 1: Timeline additions (reframe field, audio_fx op, blur reframe)

**Files:** Edit `app/timeline/schema.py` (reframe field), `app/timeline/ops.py` (+set_audio_fx), `app/render/edl_to_ffmpeg.py` (blur reframe branch). Test: extend timeline unit tests + compiler test.

**Interfaces:** `Timeline.reframe: Literal[center,face,blur]`; `set_audio_fx(tl, fx) -> (tl, diff)`; compiler `reframe="blur"` → bg-blur-contain chain.

- [ ] Steps 1–2: Failing tests. Step 3: Implement. Step 4: PASS + check. Step 5: Commit `feat: add reframe field and audio_fx op`.

### Task 2: Tool registry + MVP tool schemas

**Files:** Create `app/tools/registry.py`, `app/tools/mvp.py` (15 tool in/out models), `app/tools/executor.py` (dispatch + validation errors). Test: `tests/unit/tools/test_registry.py` (schema export shapes for claude/gemini; unknown tool; bad args → actionable error).

**Interfaces:** `ToolDef(name, description, input_model, output_model, handler)`; `registry.all()`; `to_claude_schema()`, `to_gemini_schema()` (JSON Schema from pydantic); `execute(name, args, ctx) -> output`.

- [ ] Steps 1–2: Failing tests. Step 3: Implement. Step 4: PASS + check. Step 5: Commit `feat: add MVP tool registry and schemas`.

### Task 3: Tool-calling providers + scripted fakes

**Files:** Create `app/providers/tools.py` (`ToolCallingLLM` protocol, `ToolCall` model); `app/providers/claude.py`, `gemini.py` (lazy SDKs); extend fakes with `ScriptedToolLLM`. Test: `tests/unit/test_tool_llm.py` (scripted sequence; Claude/Gemini adapters raise helpful error without SDK).

**Interfaces:** `complete_with_tools(request, tools) -> (text, [ToolCall(name, args)])`; adapters map registry schemas to vendor formats.

- [ ] Steps 1–2: Failing tests. Step 3: Implement. Step 4: PASS + check. Step 5: Commit `feat: add tool-calling provider layer`.

### Task 4: Director + Router + Executor loop

**Files:** Create `app/agent/context.py`, `director.py`, `router.py`, `executor.py` (project-state ctx: session factory, project, bundle loader). Test: `tests/unit/agent/test_director.py` (scripted multi-step; validation-error retry; iteration cap; injection transcript → zero tools).

**Interfaces:** `run_director(llm, project_ctx, message, max_iters=8) -> DirectorResult(reply, versions, previews)`; `route(message) -> direct|director`; `ProjectContext(project_id, session_factory, store, settings, enqueue)`.

- [ ] Steps 1–2: Failing tests. Step 3: Implement. Step 4: PASS + check. Step 5: Commit `feat: add director loop with router and executor`.

### Task 5: Chat API + agent_runs logging

**Files:** Migration r4 `agent_runs`; `app/agent/runs.py` (log + cost table); `app/api/chat.py` (`POST .../messages` SSE). Test: `tests/integration/test_chat_api.py` (chat turn creates version + message rows + run log; SSE event sequence).

**Interfaces:** Events: `token, tool_call, tool_result, timeline_version_created, preview_ready, ask, done, error`; run log {prompt_hash, model, tools, latency_ms, tokens, cost_usd}.

- [ ] Steps 1–2: Failing tests. Step 3: Implement (in-request director run for MVP; background in Phase 9). Step 4: PASS. Step 5: Commit `feat: add chat API with run logging`.

### Task 6: Preview-first + export wiring

**Files:** Edit executor (enqueue preview render after visual/audio edits; `preview`/`export` tools). Test: extend chat integration (edit → preview_ready URL present; export → final output URL).

**Interfaces:** Visual/audio ops set `needs_preview`; executor enqueues preview once per turn; `export` enqueues final + returns output URL (waits? No — returns render_id + job events URL; MVP demo polls).

- [ ] Steps 1–2: Failing tests. Step 3: Implement. Step 4: PASS. Step 5: Commit `feat: wire preview-first and export tools`.

### Task 7: Eval harness (30 machinery cases)

**Files:** Create `tests/evals/test_director_evals.py` + `tests/evals/cases.py` (30 cases). Skill: `write-code-eval` for the case schema.

**Interfaces:** Case{transcript, message, scripted_replies, expect: tool sequence | error recovery | no-op}; runner asserts DirectorResult properties.

- [ ] Step 1: Load `write-code-eval`, write case schema + 30 cases. Step 2: Run (FAIL expected on first pass for recovery cases). Step 3: Fix director gaps. Step 4: All pass in CI. Step 5: Commit `test: add 30-case director eval harness`.

### Task 8: MVP demo + gate

**Files:** Create `scripts/demo_mvp.py` (upload → chat turns → preview → undo → export, prints URLs). Run vs local stack.

**Interfaces:** Demo uses httpx + SSE client; asserts each step (status codes, version bumps, preview URL fetchable).

- [ ] Steps 1–3: Write + run green end-to-end (real worker needed: rebuild compose with current code).
- [ ] Gate: `make check` + integration + evals green; first-token/preview timing recorded; injection test green; `docs/gates/PHASE-5.md`; PROGRESS.md; commit `docs: phase 5 gate report`.
