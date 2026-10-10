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
from app.providers.local import AssetCatalog, LibraryItem
from app.timeline import ops, snap
from app.timeline.errors import OpError, ToolError
from app.timeline.schema import (
    EPS,
    AudioEffect,
    CaptionConfig,
    Diff,
    MusicBed,
    Overlay,
    Timeline,
)
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
    catalog: AssetCatalog | None = None


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
        clamped = _clamp_bounds(args, exc)
        if clamped is None:
            return ToolFailure(name, [_from_validation(e) for e in exc.errors()])
        try:
            params = tool.input_model.model_validate(clamped)
        except ValidationError as again:
            return ToolFailure(name, [_from_validation(e) for e in again.errors()])
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


_BOUNDS = {
    "greater_than_equal": "ge",
    "less_than_equal": "le",
    "greater_than": "gt",
    "less_than": "lt",
}


def _clamp_bounds(args: dict[str, object], exc: ValidationError) -> dict[str, object] | None:
    """Clamp numbers that are only out of range (e.g. min_silence_ms=100 -> 300).

    Small models rarely retry a rejected call, and an out-of-range number has an
    obvious nearest valid value. Anything else (missing fields, wrong types) still fails.
    """
    fixed = dict(args)
    for err in exc.errors():
        loc = err.get("loc", ())
        bound = _BOUNDS.get(str(err.get("type")))
        limit = (err.get("ctx") or {}).get(bound or "")
        if bound is None or len(loc) != 1 or not isinstance(limit, int | float):
            return None
        fixed[str(loc[0])] = limit
    return fixed


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
        case mvp.EmptyIn() if tool.name == "remove_music":
            return _edit(tool, ops.set_music(tl, None))
        case mvp.AddMusicIn():
            return _add_music(tool, p, state)
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
        case mvp.SearchAssetsIn():
            return _search_assets(tool, p, state)
        case mvp.InsertAssetIn():
            return _insert_asset(tool, p, state)
        case mvp.InsertMemeIn():
            return _insert_meme(tool, p, state)
        case mvp.AddInsertsIn():
            return _add_inserts(tool, p, state)
        case mvp.RemoveInsertIn():
            return _edit(tool, ops.remove_overlay(tl, p.insert_id))
        case mvp.AskUserIn():
            return ToolOutcome(
                tool, Effect.ASK, fields={"question": p.question, "options": p.options}
            )
    raise OpError("unsupported_tool", f"tool {tool.name} has no handler", "report this bug")


def _caption_config(tool: ToolDef, p: mvp.AddCaptionsIn, current: CaptionConfig) -> CaptionConfig:
    """add_captions sets every field; set_caption_style patches only the fields given."""
    given = p.model_dump(include=None if tool.name == "add_captions" else p.model_fields_set)
    return current.model_copy(update=given)


MAX_SILENCE_SHARE = 0.5  # one remove_silence call never deletes more than half the video

# --- inserts: deterministic placement (blueprint 3.1 step 5) --------------------------
HOOK_S = 1.0  # nothing covers the speaker before the hook lands
MIN_GAP_S = 2.0  # between inserts (start to start, and never overlapping)
SECONDS_PER_INSERT = 3.0  # density cap: at most one insert per 3 s of video
OFFSET_MS = 80  # appear just after the word ends
DURATION_MS = {"gif": (1100, 800, 1600), "image": (1800, 1200, 2500), "meme": (2200, 1500, 3000)}
DEFAULT_LAYOUT = {"gif": "pip_pop", "image": "blur_bg_contain", "meme": "blur_bg_contain"}
TYPE = {"gif": "gif", "image": "image", "meme": "meme"}


def _catalog(state: ToolState) -> AssetCatalog:
    if state.catalog is None:
        raise OpError("no_library", "asset libraries are not installed", "run `make assets`")
    return state.catalog


def _search_assets(tool: ToolDef, p: mvp.SearchAssetsIn, state: ToolState) -> ToolOutcome:
    hits = _catalog(state).search(p.kind, p.query, p.limit)
    results = [
        {
            "id": i.id,
            "title": i.title,
            "tags": i.tags[:6],
            "license": i.license,
            "commercial_use": i.commercial_use,
        }
        for i in hits
    ]
    return ToolOutcome(tool, Effect.NONE, fields={"results": results})


def _windows(tl: Timeline) -> list[tuple[float, float, str]]:
    out = []
    for o in tl.overlays:
        at = snap.output_time_of(tl, o.anchor_word, "end") + o.offset_ms / 1000.0
        out.append((at, at + o.duration_ms / 1000.0, o.id))
    return sorted(out)


def _place(tl: Timeline, kind: str, at_word: str, duration_ms: int | None) -> tuple[int, float]:
    """Validate a new insert window; returns (duration_ms, start_s). Errors teach the model."""
    default, lo, hi = DURATION_MS[kind]
    dur = min(hi, max(lo, duration_ms or default))
    if at_word not in tl.transcript.word_index:
        raise OpError(
            "unknown_word", f"word {at_word} does not exist", "use a word ID from the transcript"
        )
    try:
        start = snap.output_time_of(tl, at_word, "end") + OFFSET_MS / 1000.0
    except OpError:
        raise OpError(
            "word_cut",
            f"word {at_word} was cut from the video",
            "anchor on a word that is still in the timeline",
        ) from None
    end = start + dur / 1000.0
    if start < HOOK_S:
        raise OpError(
            "too_early",
            f"{at_word} ends at {start:.2f}s, inside the hook",
            f"anchor on a word ending after {HOOK_S:.0f}s",
        )
    if end > tl.duration_s:
        raise OpError(
            "past_end",
            f"insert would run to {end:.2f}s but the video is {tl.duration_s:.2f}s",
            "anchor on an earlier word or shorten duration_ms",
        )
    windows = _windows(tl)
    if len(windows) + 1 > max(1, int(tl.duration_s / SECONDS_PER_INSERT)):
        raise OpError(
            "too_many_inserts",
            f"{len(windows)} inserts already in {tl.duration_s:.0f}s",
            "remove one first; quality beats quantity",
        )
    for w_start, w_end, wid in windows:
        if start < w_end + EPS and w_start < end + EPS or abs(start - w_start) < MIN_GAP_S:
            raise OpError(
                "too_close",
                f"{wid} occupies {w_start:.2f}-{w_end:.2f}s",
                f"keep inserts >= {MIN_GAP_S:.0f}s apart; pick a word further away",
            )
    return dur, start


def _next_id(tl: Timeline) -> str:
    taken = {o.id for o in tl.overlays}
    n = 1
    while f"ins{n}" in taken:
        n += 1
    return f"ins{n}"


def _overlay(
    tl: Timeline,
    kind: str,
    asset_id: str,
    item: LibraryItem,
    at_word: str,
    dur: int,
    layout: str | None,
) -> Overlay:
    return Overlay(
        id=_next_id(tl),
        type=TYPE[kind],  # type: ignore[arg-type]
        asset=asset_id,
        anchor_word=at_word,
        offset_ms=OFFSET_MS,
        duration_ms=dur,
        layout=layout or DEFAULT_LAYOUT[kind],  # type: ignore[arg-type]
        license=item.license,
        attribution_required=item.attribution_required,
        attribution_text=item.attribution_text,
        commercial_use=item.commercial_use,
    )


def _insert_asset(tool: ToolDef, p: mvp.InsertAssetIn, state: ToolState) -> ToolOutcome:
    found = _catalog(state).get(p.asset_id)
    if found is None:
        raise OpError(
            "unknown_asset",
            f"asset {p.asset_id} is not in the libraries",
            "use an id returned by search_assets",
        )
    kind, item = found
    if kind == "meme":
        raise OpError(
            "use_insert_meme",
            "meme templates need captions",
            "call insert_meme with top_text/bottom_text",
        )
    dur, _ = _place(state.timeline, kind, p.at_word, p.duration_ms)
    overlay = _overlay(state.timeline, kind, item.id, item, p.at_word, dur, p.layout)
    return _edit(tool, ops.add_overlay(state.timeline, overlay))


def _insert_meme(tool: ToolDef, p: mvp.InsertMemeIn, state: ToolState) -> ToolOutcome:
    if not (p.top_text.strip() or p.bottom_text.strip()):
        raise OpError(
            "empty_meme",
            "a meme needs top_text or bottom_text",
            "write a short punchline (<= 6 words)",
        )
    catalog = _catalog(state)
    found = catalog.get(p.template_id)
    if found is None or found[0] != "meme":
        raise OpError(
            "unknown_template",
            f"{p.template_id} is not a meme template",
            "use an id from search_assets(kind='meme')",
        )
    dur, _ = _place(state.timeline, "meme", p.at_word, p.duration_ms)
    rendered_id, item = catalog.render_meme(p.template_id, p.top_text, p.bottom_text)
    overlay = _overlay(state.timeline, "meme", rendered_id, item, p.at_word, dur, None)
    return _edit(tool, ops.add_overlay(state.timeline, overlay))


def _add_inserts(tool: ToolDef, p: mvp.AddInsertsIn, state: ToolState) -> ToolOutcome:
    """Match + place each planned insert on the evolving timeline; skip with reasons."""
    catalog = _catalog(state)
    tl = state.timeline
    placed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for item in p.items:
        hits = catalog.search(item.kind, item.query, 1)
        if not hits:
            skipped.append(
                {"at_word": item.at_word, "reason": f"no {item.kind} matches '{item.query}'"}
            )
            continue
        try:
            dur, _ = _place(tl, item.kind, item.at_word, None)
            if item.kind == "meme":
                if not (item.top_text.strip() or item.bottom_text.strip()):
                    raise OpError("empty_meme", "meme without text", "add top_text/bottom_text")
                asset_id, lib_item = catalog.render_meme(
                    hits[0].id, item.top_text, item.bottom_text
                )
            else:
                asset_id, lib_item = hits[0].id, hits[0]
            overlay = _overlay(tl, item.kind, asset_id, lib_item, item.at_word, dur, None)
            tl, _ = ops.add_overlay(tl, overlay)
        except OpError as exc:
            skipped.append({"at_word": item.at_word, "reason": exc.code})
            continue
        placed.append(
            {
                "insert_id": overlay.id,
                "at_word": item.at_word,
                "kind": item.kind,
                "title": lib_item.title,
            }
        )
    if not placed:
        return ToolOutcome(
            tool,
            Effect.NONE,
            fields={"placed": [], "skipped": skipped, "summary": "nothing placed"},
        )
    diff = Diff(op="add_inserts", details={"placed": ",".join(x["insert_id"] for x in placed)})
    return ToolOutcome(
        tool, Effect.NEW_VERSION, tl, diff, {"op": tool.name, "placed": placed, "skipped": skipped}
    )


def _add_music(tool: ToolDef, p: mvp.AddMusicIn, state: ToolState) -> ToolOutcome:
    catalog = _catalog(state)
    tracks = catalog.music(p.genre, p.mood, state.timeline.duration_s)
    if not tracks:
        raise OpError(
            "no_music", f"no tracks for genre {p.genre!r}", f"genres: {', '.join(catalog.genres())}"
        )
    track = tracks[0]
    bed = MusicBed(
        asset=track.id,
        license=track.license,
        attribution_required=track.attribution_required,
        attribution_text=track.attribution_text,
        level_db=p.level_db,
        commercial_use=track.commercial_use,
    )
    return _edit(
        tool,
        ops.set_music(state.timeline, bed),
        track=track.title,
        genre=track.genre,
        license=track.license,
        attribution=track.attribution_text,
    )


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
    if not state.speech:
        raise OpError(
            "no_vad",
            "voice-activity analysis is missing, so silence cannot be told from speech",
            "wait for analysis to finish, or cut specific words with delete_range",
        )
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
    if removed > tl.duration_s * MAX_SILENCE_SHARE:
        raise OpError(
            "suspicious_silence",
            f"would remove {removed:.1f}s of {tl.duration_s:.1f}s; analysis is probably wrong",
            "do not cut; tell the user the audio could not be segmented reliably",
        )
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
