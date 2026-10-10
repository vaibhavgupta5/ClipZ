"""DbWorkspace: the Director's view of one project in MongoDB + local storage."""

import json
import uuid
from pathlib import Path

from app import db as db_ops
from app.analysis.models import AnalysisBundle
from app.db import Database
from app.models import Analysis
from app.models import Timeline as TimelineRow
from app.providers.local import AssetCatalog
from app.services.renders import request_render
from app.settings import get_settings
from app.storage import Storage
from app.timeline.versions import get_current, load_version, save_version
from app.timeline.versions import redo as redo_version
from app.timeline.versions import undo as undo_version
from app.tools.executor import Effect, ToolOutcome, ToolState


async def latest_bundle(
    db: Database, store: Storage, project_id: uuid.UUID
) -> AnalysisBundle | None:
    """Newest analysis bundle for a project (None before analysis finishes)."""
    row = await db_ops.find_one(db, Analysis, {"project_id": project_id}, sort=[("created_at", -1)])
    if row is None:
        return None
    try:
        return AnalysisBundle.model_validate(json.loads(store.get_bytes(row.bundle_key)))
    except KeyError:
        return None


class DbWorkspace:
    """Workspace over one project's timeline; analysis signals are loaded once per turn."""

    def __init__(
        self,
        db: Database,
        project_id: uuid.UUID,
        timeline: TimelineRow,
        bundle: AnalysisBundle | None,
    ) -> None:
        self.db = db
        self.project_id = project_id
        self.row = timeline
        self.bundle = bundle
        settings = get_settings()
        self.catalog = AssetCatalog(
            settings.ASSETS_DIR, Path(settings.WORK_DIR) / "cache" / "memes"
        )

    async def state(self) -> ToolState:
        """Current timeline + VAD speech + filler candidates."""
        tl = await load_version(self.db, self.row.id, await self.current_version())
        speech = list(self.bundle.speech) if self.bundle else []
        fillers = list(self.bundle.fillers) if self.bundle else []
        return ToolState(tl, speech, fillers, catalog=self.catalog)

    async def current_version(self) -> int:
        """Current version pointer."""
        return await get_current(self.db, self.row.id)

    async def commit(self, outcome: ToolOutcome) -> int:
        """Persist the outcome's timeline as a child of the current version."""
        assert outcome.timeline is not None
        parent = await self.current_version()
        version, _ = await save_version(self.db, self.row.id, outcome.timeline, parent=parent)
        return version

    async def move(self, effect: Effect) -> int:
        """Undo/redo pointer move."""
        if effect == Effect.UNDO:
            return await undo_version(self.db, self.row.id)
        return await redo_version(self.db, self.row.id)

    async def dub(self, language: str) -> str:
        """Enqueue a dubbing job; it commits a new version when ElevenLabs finishes."""
        from app.workers.dub import request_dub

        job = await request_dub(
            self.db, get_settings().REDIS_URL, self.project_id, self.row.id, language
        )
        return str(job.id)

    async def render(self, profile: str) -> tuple[str, str]:
        """Enqueue a render of the current version."""
        version = await self.current_version()
        render, job = await request_render(self.db, self.project_id, self.row.id, version, profile)
        return str(render.id), str(job.id)
