"""In-memory Workspace for Director tests and evals (no DB, no queue)."""

from app.analysis.models import FillerHit, SpeechRange
from app.timeline.schema import Timeline
from app.timeline.versions import VersionLog
from app.tools.executor import Effect, ToolOutcome, ToolState


class MemoryWorkspace:
    """Versions in a pure VersionLog; renders recorded, never executed."""

    def __init__(
        self,
        tl: Timeline,
        speech: list[SpeechRange] | None = None,
        fillers: list[FillerHit] | None = None,
    ) -> None:
        self.log = VersionLog().append(tl)
        self.speech = speech or []
        self.fillers = fillers or []
        self.render_calls: list[str] = []

    def timeline(self) -> Timeline:
        entry = self.log.get(self.log.current)
        assert entry is not None
        return Timeline.model_validate(entry.edl).model_copy(update={"version": entry.version})

    async def state(self) -> ToolState:
        return ToolState(self.timeline(), self.speech, self.fillers)

    async def current_version(self) -> int:
        return self.log.current

    async def commit(self, outcome: ToolOutcome) -> int:
        assert outcome.timeline is not None
        self.log = self.log.append(outcome.timeline)
        return self.log.current

    async def move(self, effect: Effect) -> int:
        self.log = self.log.undo() if effect == Effect.UNDO else self.log.redo()
        return self.log.current

    async def render(self, profile: str) -> tuple[str, str]:
        self.render_calls.append(profile)
        n = len(self.render_calls)
        return f"r{n}", f"j{n}"
