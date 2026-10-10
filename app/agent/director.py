"""Director: the tool loop. LLM proposes calls -> executor validates -> workspace persists.

The loop is bounded (``max_iters`` model turns), every call is validated before
any version is written, and at most one preview render is queued per chat turn.
Persistence and rendering go through ``Workspace`` so the loop is testable offline.
"""

import dataclasses
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from app.agent import coverage
from app.agent.context import timeline_state
from app.agent.critic import CriticReport, evaluate_preview
from app.agent.router import route
from app.inserts import keywords
from app.providers.base import LLMProvider
from app.providers.local import LibraryItem
from app.providers.tools import (
    AgentMessage,
    ToolCall,
    ToolCallingLLM,
    ToolResult,
    ToolTurnRequest,
    Usage,
)
from app.timeline.schema import Timeline
from app.timeline.summary import summarize
from app.tools import mvp
from app.tools.executor import (
    GENERATING_TOOLS,
    Effect,
    ToolFailure,
    ToolOutcome,
    ToolState,
    execute,
)
from app.tools.registry import ELEVENLABS_TOOLS, get_tool, tool_specs

MAX_ITERS = 8
MAX_FAILURES_PER_TOOL = 2

EventType = Literal[
    "token",
    "tool_call",
    "tool_result",
    "timeline_version_created",
    "preview_queued",
    "render_queued",
    "dub_queued",
    "ask",
    "done",
    "render_progress",
    "preview_ready",
    "render_ready",
    "render_failed",
    "critic_feedback",
    "error",
]


@dataclass(frozen=True)
class Event:
    """One streamed chat event (SSE payload)."""

    type: EventType
    data: dict[str, object] = field(default_factory=dict)


Emit = Callable[[Event], None]


class Workspace(Protocol):
    """Project state the Director may read and change."""

    async def state(self) -> ToolState:
        """Current timeline + analysis signals."""
        ...

    async def current_version(self) -> int:
        """Current version pointer."""
        ...

    async def commit(self, outcome: ToolOutcome) -> int:
        """Persist ``outcome.timeline`` as a new version; return its number."""
        ...

    async def move(self, effect: Effect) -> int:
        """Undo/redo pointer move; return the version now current."""
        ...

    async def render(self, profile: str) -> tuple[str, str]:
        """Enqueue a render of the current version; return (render_id, job_id)."""
        ...

    async def dub(self, language: str) -> str:
        """Enqueue dubbing of the current version into ``language``; return the job id."""
        ...


class AudioGenerator(Protocol):
    """Generated audio (ElevenLabs behind ``GeneratedAudioStore``; fakes in tests)."""

    async def sound(self, prompt: str, duration_s: float) -> LibraryItem:
        """A sound effect for the description (cached by content hash)."""
        ...

    async def voice(self, text: str) -> LibraryItem:
        """A voiceover of the text (cached by content hash)."""
        ...


@dataclass
class DirectorResult:
    """What one chat turn did."""

    reply: str = ""
    versions: list[int] = field(default_factory=list)
    renders: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    question: str | None = None
    iterations: int = 0
    stopped: Literal["answered", "asked", "cap", "max_tokens", "refusal", "direct"] = "answered"
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    history: list[AgentMessage] = field(default_factory=list)
    coverage_followup: list[str] = field(default_factory=list)  # gaps the model was told about
    autofixed: list[str] = field(default_factory=list)  # tools the Director applied itself
    critic_reports: list[CriticReport] = field(default_factory=list)  # critic evaluations


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_read_tokens=a.cache_read_tokens + b.cache_read_tokens,
        cache_write_tokens=a.cache_write_tokens + b.cache_write_tokens,
        thought_tokens=a.thought_tokens + b.thought_tokens,
    )


class _Turn:
    """Mutable bookkeeping for one chat turn."""

    def __init__(
        self,
        ws: Workspace,
        emit: Emit,
        result: DirectorResult,
        generator: AudioGenerator | None = None,
    ) -> None:
        self.ws = ws
        self.generator = generator
        self.emit = emit
        self.result = result
        self.failures: dict[str, int] = {}
        self.needs_preview = False
        self.rendered = False
        self.log: list[tuple[str, bool, str]] = []  # (tool, ok, short reason)
        self.exported_version: int | None = None  # version the last final export rendered

    async def call(self, call: ToolCall) -> ToolResult:
        """Execute one tool call end to end and build the model-facing result."""
        self.emit(Event("tool_call", {"id": call.id, "name": call.name, "args": call.args}))
        self.result.tools.append(call.name)
        if self.failures.get(call.name, 0) >= MAX_FAILURES_PER_TOOL:
            return self._error(
                call, f"{call.name} failed {MAX_FAILURES_PER_TOOL} times; stop and tell the user"
            )
        started = time.monotonic()
        state = await self.ws.state()
        if call.name in GENERATING_TOOLS:
            state = await self._prepare(call, state)
        outcome = execute(call.name, call.args, state)
        if isinstance(outcome, ToolFailure):
            self.failures[call.name] = self.failures.get(call.name, 0) + 1
            code = outcome.errors[0].code if outcome.errors else "error"
            self.log.append((call.name, False, code))
            return self._error(call, outcome.message(), code, _ms(started))
        facts = await self._apply(outcome)
        if outcome.effect != Effect.NONE:
            self.log.append((call.name, True, ""))
        payload = outcome.output(**facts).model_dump(mode="json")
        result = {"id": call.id, "name": call.name, "ok": True, "result": payload}
        self.emit(Event("tool_result", {**result, "ms": _ms(started)}))
        return ToolResult(call_id=call.id, name=call.name, content=json.dumps(payload))

    async def _prepare(self, call: ToolCall, state: ToolState) -> ToolState:
        """Generate the clip a generating tool needs. Any failure leaves ``prepared`` empty
        and the executor answers with ``generation_unavailable`` plus a fallback hint."""
        if self.generator is None:
            return state
        try:
            params = get_tool(call.name).input_model.model_validate(call.args)
        except ValueError:
            return state  # the executor reports the validation errors
        try:
            if isinstance(params, mvp.GenerateSfxIn):
                item = await self.generator.sound(params.prompt, params.duration_s)
            elif isinstance(params, mvp.AddVoiceoverIn):
                item = await self.generator.voice(params.text)
            else:
                return state
        except Exception as exc:  # provider failures must not break the turn
            self.emit(Event("error", {"tool": call.name, "message": str(exc)[:200]}))
            return state
        if state.catalog is not None:
            state.catalog.reload("generated")
        return dataclasses.replace(state, prepared=item)

    async def _apply(self, outcome: ToolOutcome) -> dict[str, object]:
        match outcome.effect:
            case Effect.NEW_VERSION:
                version = await self.ws.commit(outcome)
                self.result.versions.append(version)
                self.needs_preview = self.needs_preview or outcome.needs_preview
                diff = outcome.diff.model_dump() if outcome.diff else {}
                self.emit(Event("timeline_version_created", {"version": version, "diff": diff}))
                return {"version": version}
            case Effect.UNDO | Effect.REDO:
                before = await self.ws.current_version()
                version = await self.ws.move(outcome.effect)
                if version != before:
                    self.result.versions.append(version)
                    self.needs_preview = True
                    self.emit(
                        Event(
                            "timeline_version_created",
                            {"version": version, "diff": {"op": outcome.effect.value}},
                        )
                    )
                state = await self.ws.state()
                return {"version": version, "summary": summarize(state.timeline)}
            case Effect.RENDER:
                profile = str(outcome.fields.get("profile", "preview"))
                render_id, job_id = await self.ws.render(profile)
                self.result.renders.append(render_id)
                self.rendered = self.rendered or profile == "preview"
                if profile == "final":
                    self.exported_version = await self.ws.current_version()
                kind: EventType = "preview_queued" if profile == "preview" else "render_queued"
                self.emit(
                    Event(kind, {"render_id": render_id, "job_id": job_id, "profile": profile})
                )
                return {"render_id": render_id, "job_id": job_id}
            case Effect.DUB:
                language = str(outcome.fields["language"])
                job_id = await self.ws.dub(language)
                self.emit(Event("dub_queued", {"job_id": job_id, "language": language}))
                return {
                    "job_id": job_id,
                    "note": f"dubbing into {language} in the background; it becomes a new "
                    "version when ready",
                }
            case Effect.ASK:
                self.result.question = str(outcome.fields["question"])
                self.emit(Event("ask", dict(outcome.fields)))
                return {}
            case _:
                return {"version": await self.ws.current_version()}

    def _error(
        self, call: ToolCall, message: str, code: str = "rejected", ms: int = 0
    ) -> ToolResult:
        data = {"id": call.id, "name": call.name, "ok": False, "error": message}
        self.emit(Event("tool_result", {**data, "code": code, "ms": ms}))
        return ToolResult(call_id=call.id, name=call.name, content=message, is_error=True)

    async def finish(self) -> None:
        """Queue one preview if this turn changed something visible and none was rendered;
        re-export if edits landed after this turn's export (it would be stale)."""
        if self.exported_version is not None:
            current = await self.ws.current_version()
            if current != self.exported_version:
                render_id, job_id = await self.ws.render("final")
                self.result.renders.append(render_id)
                self.exported_version = current
                data: dict[str, object] = {
                    "render_id": render_id,
                    "job_id": job_id,
                    "profile": "final",
                }
                self.emit(Event("render_queued", data))
            self.rendered = True  # the final export of this version doubles as the preview
        if self.needs_preview and not self.rendered:
            render_id, job_id = await self.ws.render("preview")
            self.result.renders.append(render_id)
            self.emit(
                Event(
                    "preview_queued",
                    {"render_id": render_id, "job_id": job_id, "profile": "preview"},
                )
            )


async def run_director(
    llm: ToolCallingLLM,
    ws: Workspace,
    system: str,
    message: str,
    history: list[AgentMessage] | None = None,
    emit: Emit | None = None,
    max_iters: int = MAX_ITERS,
    generator: AudioGenerator | None = None,
    enable_critic: bool = False,
    max_critic_iters: int = 2,
    critic_llm: LLMProvider | None = None,
) -> DirectorResult:
    """Handle one user chat message. Always terminates within ``max_iters`` model turns.

    Without a ``generator`` (no ElevenLabs key) the generated-audio tools are hidden.
    When ``enable_critic=True``, reviews the timeline after edits and self-corrects up
    to ``max_critic_iters``.
    """
    sink: Emit = emit or (lambda _e: None)
    result = DirectorResult(model=llm.model)
    turn = _Turn(ws, sink, result, generator)

    direct = route(message)
    if direct is not None:
        res = await turn.call(direct)
        await turn.finish()
        result.reply = _direct_reply(direct.name, res)
        result.stopped = "direct"
        sink(Event("done", {"reply": result.reply}))
        return result

    start_state = await ws.state()
    tl = start_state.timeline
    visuals = (
        keywords.describe(keywords.find_cues(tl, start_state.catalog))
        if start_state.catalog is not None
        else ""
    )
    msgs = [
        *(history or []),
        AgentMessage(role="user", text=f"{message}\n\n{timeline_state(tl, visuals)}"),
    ]
    tools = tool_specs(exclude=frozenset() if generator else ELEVENLABS_TOOLS)
    reviewed = False
    critic_count = 0
    for i in range(max_iters):
        result.iterations = i + 1
        reply = await llm.complete_with_tools(
            ToolTurnRequest(system=system, messages=msgs, tools=tools),
            on_text=lambda t: sink(Event("token", {"text": t})),
        )
        result.usage = _add(result.usage, reply.usage)
        result.model = reply.model or result.model
        msgs.append(reply.as_message())
        if reply.stop_reason in ("max_tokens", "refusal"):
            # never run calls from a truncated or refused turn
            result.stopped = reply.stop_reason
            result.reply = reply.text or "I couldn't complete that request."
            break
        if not reply.calls:
            gaps = coverage.missing(
                message, {t for t, ok, _ in turn.log if ok}, (await ws.state()).timeline
            )
            if gaps and reviewed:
                # Still skipped after the follow-up: apply the safe defaults ourselves.
                await _autofix(turn, gaps, message, tl)
                gaps = []
            if gaps:
                # One targeted follow-up for parts of the request the model skipped.
                reviewed = True
                result.coverage_followup = gaps
                note = (
                    "Not done yet from the user's request: " + "; ".join(gaps) + ". "
                    "Call the tools for these now. Skip one only if it is truly impossible, "
                    "and then say why."
                )
                msgs.append(AgentMessage(role="user", text=note))
                continue

            if enable_critic and critic_count < max_critic_iters and len(result.versions) > 0:
                current_tl = (await ws.state()).timeline
                report = await evaluate_preview(critic_llm, current_tl, user_prompt=message)
                result.critic_reports.append(report)
                sink(Event("critic_feedback", report.model_dump(mode="json")))
                if report.verdict == "needs_edits" and report.suggested_edits:
                    critic_count += 1
                    header = (
                        f"Video Critic Review (Score: {report.overall_score}/10, "
                        f"Verdict: {report.verdict}):\n"
                    )
                    critic_msg = (
                        header
                        + "\n".join(f"- Issue: {iss}" for iss in report.issues)
                        + "\nSuggested Fixes:\n"
                        + "\n".join(f"- {fix}" for fix in report.suggested_edits)
                        + "\nPlease call the tools to apply these improvements now."
                    )
                    msgs.append(AgentMessage(role="user", text=critic_msg))
                    continue

            result.reply = reply.text
            break
        results = [await turn.call(c) for c in reply.calls]
        msgs.append(AgentMessage(role="user", results=results))
        if result.question is not None:
            result.stopped = "asked"
            result.reply = reply.text or result.question
            break
    else:
        result.stopped = "cap"
        result.reply = f"I stopped after {max_iters} steps; tell me what to do next."
    await turn.finish()
    result.reply = _grounded(result.reply, turn.log)
    result.history = msgs
    sink(Event("done", {"reply": result.reply, "stopped": result.stopped}))
    return result


async def _autofix(turn: _Turn, gaps: list[str], message: str, tl: Timeline) -> None:
    """Deterministic defaults for requested items the model would not do (music, SFX...)."""
    text = " ".join(w.text for w in tl.transcript.words)
    catalog = (await turn.ws.state()).catalog
    genres = catalog.genres() if catalog is not None else None
    calls = coverage.autofix_calls(gaps, message, text, genres)
    turn.result.autofixed = [name for name, _ in calls]
    for n, (name, args) in enumerate(calls, start=1):
        await turn.call(ToolCall(id=f"autofix{n}", name=name, args=args))


def _grounded(reply: str, log: list[tuple[str, bool, str]]) -> str:
    """Append what actually happened, so the reply can never claim an edit that failed."""
    if not log:
        return reply
    done = sorted({t for t, ok, _ in log if ok})
    failed = sorted({f"{t} ({why})" for t, ok, why in log if not ok and t not in done})
    lines = []
    if done:
        lines.append("Applied: " + ", ".join(done) + ".")
    if failed:
        lines.append("Not applied: " + ", ".join(failed) + ".")
    return f"{reply}\n\n" + " ".join(lines) if reply else " ".join(lines)


def _direct_reply(name: str, res: ToolResult) -> str:
    if res.is_error:
        return res.content
    data = json.loads(res.content)
    if name in ("undo", "redo"):
        return f"{name.capitalize()} done; now at version {data.get('version')}."
    return f"{name} queued (render {data.get('render_id')})."
