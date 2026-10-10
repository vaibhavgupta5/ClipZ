"""Tool executor: validate args, run pure timeline ops, return an outcome. No I/O.

The agent layer owns persistence: it saves ``outcome.timeline`` as a new version
(or moves the undo/redo pointer, or enqueues a render), then calls
``outcome.output(...)`` with the facts only it knows (version, render IDs).
Every failure comes back as ``ToolFailure`` with errors written for the LLM.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ValidationError

from app.analysis.models import FillerHit, SpeechRange
from app.timeline import ops
from app.timeline.errors import OpError, ToolError
from app.timeline.schema import EPS, AudioEffect, CaptionConfig, Diff, Timeline
from app.timeline.summary import summarize
from app.timeline.validate import validate_timeline
from app.tools import mvp
from app.tools.mvp import ToolDef
from app.tools.registry import ToolNotFound, get_tool


class Effect(StrEnum):
    """What the agent layer must do with an outcome."""

    NONE = "none"  # read-only or no-op: report current version
    NEW_VERSION = "new_version"  # persist outcome.timeline
    UNDO = "undo"
    REDO = "redo"
    RENDER = "render"  # enqueue a render (preview/export)
    ASK = "ask"  # stop the loop and wait for the user


@dataclass(frozen=True)
class ToolState:
    """Everything a tool may read: current timeline + analysis signals."""

    timeline: Timeline
    speech: list[SpeechRange] = field(default_factory=list)
    fillers: list[FillerHit] = field(default_factory=list)


@dataclass(frozen=True)
class ToolOutcome:
    """Successful tool call, pending persistence by the agent layer."""

    tool: ToolDef
    effect: Effect
    timeline: Timeline | None = None
    diff: Diff | None = None
    fields: dict[str, object] = field(default_factory=dict)

    @property
    def needs_preview(self) -> bool:
        """True when a persisted change is visual/audio (enqueue one preview per turn)."""
        return self.effect == Effect.NEW_VERSION and self.tool.needs_preview

    def output(self, **facts: object) -> BaseModel:
        """Build the tool's output model from computed fields + agent-supplied facts."""
        data = dict(self.fields)
        if self.timeline is not None and "summary" in self.tool.output_model.model_fields:
            data.setdefault("summary", summarize(self.timeline))
        data.update(facts)
        return self.tool.output_model.model_validate(data)


@dataclass(frozen=True)
class ToolFailure:
    """Rejected tool call; ``errors`` go back to the model verbatim."""

    name: str
    errors: list[ToolError]

    def message(self) -> str:
        """Compact, actionable text for the model."""
        return "\n".join(f"{e.code} ({e.field}): {e.message}. Fix: {e.hint}" for e in self.errors)


def execute(name: str, args: dict[str, object], state: ToolState) -> ToolOutcome | ToolFailure:
    """Validate ``args`` for tool ``name`` and apply it to ``state``. Never raises for bad input."""
    try:
        tool = get_tool(name)
    except ToolNotFound as exc:
        return ToolFailure(
            name,
            [
                ToolError(
                    code="unknown_tool",
                    field=name,
                    message=str(exc.args[0]),
                    hint="call one of the listed tools",
                )
            ],
        )
    try:
        params = tool.input_model.model_validate(args)
    except ValidationError as exc:
        return ToolFailure(name, [_from_validation(e) for e in exc.errors()])
    try:
        outcome = _dispatch(tool, params, state)
    except OpError as exc:
        return ToolFailure(
            name,
            [
                ToolError(
                    code=exc.code,
                    field=name,
                    message=str(exc),
                    hint=exc.hint or "check the arguments",
                )
            ],
        )
    if outcome.timeline is not None:
        errors = validate_timeline(outcome.timeline)
        if errors:
            return ToolFailure(name, errors)
    return outcome


def _from_validation(err: object) -> ToolError:
    assert isinstance(err, dict)
    loc = ".".join(str(p) for p in err.get("loc", ())) or "args"
    return ToolError(
        code="bad_argument",
        field=loc,
        message=str(err.get("msg", "invalid value")),
        hint=f"got {err.get('input')!r}; see the tool's input schema",
    )


def _edit(tool: ToolDef, result: tuple[Timeline, Diff], **fields: object) -> ToolOutcome:
    tl, diff = result
    return ToolOutcome(tool, Effect.NEW_VERSION, tl, diff, {"op": tool.name, **fields})


def _dispatch(tool: ToolDef, p: BaseModel, state: ToolState) -> ToolOutcome:
    tl = state.timeline
    match p:
        case mvp.EmptyIn() if tool.name == "get_timeline":
            return ToolOutcome(
                tool,
                Effect.NONE,
                fields={"summary": summarize(tl), "duration_s": tl.duration_s},
            )
        case mvp.EmptyIn() if tool.name in ("undo", "redo"):
            return ToolOutcome(tool, Effect(tool.name), fields={"op": tool.name})
        case mvp.TrimIn() if tool.name == "delete_range":
            return _edit(tool, ops.delete_range(tl, p.start_word, p.end_word, p.pad_ms))
        case mvp.TrimIn():  # trim | cut_by_transcript
            return _edit(tool, ops.trim(tl, p.start_word, p.end_word, p.pad_ms))
        case mvp.RemoveSilenceIn():
            return _remove_silence(tool, p, state)
        case mvp.RemoveFillersIn():
            return _remove_fillers(tool, p, state)
        case mvp.AddCaptionsIn():
            return _edit(tool, ops.set_captions(tl, _caption_config(tool, p, tl.captions)))
        case mvp.DenoiseIn():
            fx = [e for e in tl.audio_fx if e.type != "denoise"]
            if p.enabled:
                fx.insert(0, AudioEffect(type="denoise"))
            return _edit(tool, ops.set_audio_fx(tl, fx))
        case mvp.LoudnessIn():
            fx = [e for e in tl.audio_fx if e.type != "loudnorm"]
            fx.append(AudioEffect(type="loudnorm", params={"lufs": p.target_lufs}))
            return _edit(tool, ops.set_audio_fx(tl, fx))
        case mvp.ReframeIn():
            return _edit(tool, ops.set_reframe(tl, p.mode))
        case mvp.PreviewIn():
            _check_range(tl, p.start_word, p.end_word)
            return ToolOutcome(tool, Effect.RENDER, fields={"profile": "preview"})
        case mvp.ExportIn():
            if p.monetized:
                _check_commercial(tl)
            return ToolOutcome(tool, Effect.RENDER, fields={"profile": p.profile})
        case mvp.AskUserIn():
            return ToolOutcome(
                tool, Effect.ASK, fields={"question": p.question, "options": p.options}
            )
    raise OpError("unsupported_tool", f"tool {tool.name} has no handler", "report this bug")


def _caption_config(tool: ToolDef, p: mvp.AddCaptionsIn, current: CaptionConfig) -> CaptionConfig:
    """add_captions sets every field; set_caption_style patches only the fields given."""
    given = p.model_dump(include=None if tool.name == "add_captions" else p.model_fields_set)
    return current.model_copy(update=given)


def personal_use_assets(tl: Timeline) -> list[str]:
    """IDs of inserts / music that are personal-use only (license unknown)."""
    ids = [o.id for o in tl.overlays if not o.commercial_use]
    if tl.music is not None and not tl.music.commercial_use:
        ids.append(f"music:{tl.music.asset}")
    return ids


def _check_commercial(tl: Timeline) -> None:
    blocked = personal_use_assets(tl)
    if blocked:
        raise OpError(
            "personal_use_assets",
            f"monetised export blocked: {', '.join(blocked)} are personal-use only",
            "remove or swap those inserts, or export with monetized=false if the user "
            "confirms the reel will not be monetised",
        )


def _check_range(tl: Timeline, start: str | None, end: str | None) -> None:
    if (start is None) != (end is None):
        raise OpError(
            "half_range",
            "give both start_word and end_word, or neither",
            "omit both to preview everything",
        )
    for wid in (start, end):
        if wid is not None and wid not in tl.transcript.word_index:
            raise OpError(
                "unknown_word", f"word {wid} does not exist", "use word IDs from the transcript"
            )


def _remove_silence(tool: ToolDef, p: mvp.RemoveSilenceIn, state: ToolState) -> ToolOutcome:
    """Cut gaps that are silent per VAD AND word-free, keeping ``padding_ms`` each side."""
    tl = state.timeline
    lo_src = min(c.src_in for c in tl.video_clips)
    hi_src = max(c.src_out for c in tl.video_clips)
    if not tl.transcript.words:
        return ToolOutcome(
            tool,
            Effect.NONE,
            fields={"removed_ms": 0, "ranges": 0, "summary": "no speech detected; nothing removed"},
        )
    busy = sorted(
        [(s.start, s.end) for s in state.speech] + [(w.start, w.end) for w in tl.transcript.words]
    )
    gaps: list[tuple[float, float]] = []
    cursor = lo_src
    for a, b in busy:
        if a > cursor:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if hi_src > cursor:
        gaps.append((cursor, hi_src))
    pad = p.padding_ms / 1000.0
    cuts = [
        (a + pad, b - pad)
        for a, b in gaps
        if (b - a) * 1000 >= p.min_silence_ms and (b - pad) - (a + pad) > EPS
    ]
    # only count what is actually on the timeline (gaps may span already-cut source)
    removed = sum(_overlap_on_timeline(tl, a, b) for a, b in cuts)
    if removed <= EPS:
        return ToolOutcome(
            tool,
            Effect.NONE,
            fields={
                "removed_ms": 0,
                "ranges": 0,
                "summary": f"no silence of {p.min_silence_ms} ms or more left",
            },
        )
    return _edit(
        tool, ops.delete_gaps(tl, cuts), removed_ms=round(removed * 1000), ranges=len(cuts)
    )


def _overlap_on_timeline(tl: Timeline, lo: float, hi: float) -> float:
    return sum(max(0.0, min(c.src_out, hi) - max(c.src_in, lo)) / c.speed for c in tl.video_clips)


def _remove_fillers(tool: ToolDef, p: mvp.RemoveFillersIn, state: ToolState) -> ToolOutcome:
    """Delete analysis-flagged fillers still on the timeline, optionally one language only."""
    tl = state.timeline
    if p.lang not in ("auto", "en", "hi"):
        raise OpError("bad_lang", f"language {p.lang!r} unsupported", "use en, hi or auto")
    index = tl.transcript.word_index
    ids = [
        h.word_id
        for h in state.fillers
        if h.word_id in index
        and (p.lang == "auto" or index[h.word_id].lang == p.lang)
        and _overlap_on_timeline(tl, index[h.word_id].start, index[h.word_id].end) > EPS
    ]
    if not ids:
        return ToolOutcome(
            tool, Effect.NONE, fields={"removed": [], "summary": "no filler words found"}
        )
    return _edit(tool, ops.delete_words(tl, ids), removed=ids)


__all__ = [
    "Effect",
    "ToolFailure",
    "ToolOutcome",
    "ToolState",
    "execute",
]
