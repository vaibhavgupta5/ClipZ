"""Timeline versioning: pure pointer log + Postgres persistence.

Undo/redo are pointer moves on timelines.current_version. Edit-after-undo
appends a new version whose parent is the pointer (old redo chain kept, unreachable).
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Timeline as TimelineRow
from app.models import TimelineVersion as TimelineVersionRow
from app.timeline.schema import Timeline


@dataclass(frozen=True)
class VersionEntry:
    """One immutable snapshot reference."""

    version: int
    parent: int | None
    edl: dict[str, Any]


@dataclass(frozen=True)
class VersionLog:
    """Pure undo/redo log. All methods return new logs (immutable)."""

    entries: tuple[VersionEntry, ...] = field(default_factory=tuple)
    current: int = 0

    def append(self, tl: Timeline) -> "VersionLog":
        """Append a snapshot; parent = current pointer (None for v1)."""
        existing = [e.version for e in self.entries]
        version = (max(existing) if existing else 0) + 1
        parent = self.current or None
        entry = VersionEntry(version=version, parent=parent, edl=tl.model_dump(mode="json"))
        return VersionLog(entries=(*self.entries, entry), current=version)

    def undo(self) -> "VersionLog":
        """Move pointer back one version (min: first)."""
        if self.current <= 1:
            return self
        return VersionLog(entries=self.entries, current=self.current - 1)

    def redo(self) -> "VersionLog":
        """Move pointer forward only along the linear chain (no redo past a branch)."""
        versions = sorted(e.version for e in self.entries)
        nxt = [v for v in versions if v > self.current]
        if not nxt:
            return self
        # Redo is valid only if the next version's parent is the pointer.
        entry = next(e for e in self.entries if e.version == nxt[0])
        if entry.parent != self.current:
            return self
        return VersionLog(entries=self.entries, current=nxt[0])

    def get(self, version: int) -> VersionEntry | None:
        """Fetch an entry by version number."""
        return next((e for e in self.entries if e.version == version), None)

    def parent_of(self, version: int) -> int | None:
        """Parent version number (None for v1)."""
        entry = self.get(version)
        return entry.parent if entry else None


async def save_version(
    session: AsyncSession, timeline_id: uuid.UUID, tl: Timeline, parent: int | None
) -> tuple[int, Timeline]:
    """Persist an EDL snapshot stamped with its version; bump current pointer."""
    max_v = await session.scalar(
        select(func.max(TimelineVersionRow.version)).where(
            TimelineVersionRow.timeline_id == timeline_id
        )
    )
    version = (max_v or 0) + 1
    stamped = tl.model_copy(update={"version": version})
    row = await session.get(TimelineRow, timeline_id)
    if row is None:
        raise ValueError(f"timeline {timeline_id} does not exist")
    session.add(
        TimelineVersionRow(
            timeline_id=timeline_id,
            version=version,
            edl=stamped.model_dump(mode="json"),
            parent_version=parent,
        )
    )
    row.current_version = version
    await session.flush()
    return (version, stamped)


async def load_version(session: AsyncSession, timeline_id: uuid.UUID, version: int) -> Timeline:
    """Load one snapshot as a Timeline."""
    row = await session.scalar(
        select(TimelineVersionRow).where(
            TimelineVersionRow.timeline_id == timeline_id,
            TimelineVersionRow.version == version,
        )
    )
    if row is None:
        raise ValueError(f"timeline {timeline_id} has no version {version}")
    edl: dict[str, Any] = dict(row.edl)
    return Timeline.model_validate(edl)


async def list_versions(session: AsyncSession, timeline_id: uuid.UUID) -> list[VersionEntry]:
    """All snapshots for a timeline, oldest first."""
    rows = (
        await session.scalars(
            select(TimelineVersionRow)
            .where(TimelineVersionRow.timeline_id == timeline_id)
            .order_by(TimelineVersionRow.version)
        )
    ).all()
    return [VersionEntry(version=r.version, parent=r.parent_version, edl=dict(r.edl)) for r in rows]


async def get_current(session: AsyncSession, timeline_id: uuid.UUID) -> int:
    """Current version pointer."""
    row = await session.get(TimelineRow, timeline_id)
    if row is None:
        raise ValueError(f"timeline {timeline_id} does not exist")
    return row.current_version


async def set_current(session: AsyncSession, timeline_id: uuid.UUID, version: int) -> None:
    """Move the pointer (undo/redo) without deleting history."""
    exists = await session.scalar(
        select(func.count())
        .select_from(TimelineVersionRow)
        .where(TimelineVersionRow.timeline_id == timeline_id, TimelineVersionRow.version == version)
    )
    if not exists:
        raise ValueError(f"timeline {timeline_id} has no version {version}")
    row = await session.get(TimelineRow, timeline_id)
    assert row is not None  # version rows FK-require the timeline row
    row.current_version = version
    await session.flush()
