"""Timeline versioning: pure pointer log + Postgres persistence.

Undo/redo are pointer moves on timelines.current_version (MongoDB). Edit-after-undo
appends a new version whose parent is the pointer (old redo chain kept, unreachable).
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app import db as db_ops
from app.db import Database, DuplicateKeyError
from app.models import Timeline as TimelineDoc
from app.models import TimelineVersion as TimelineVersionDoc
from app.timeline.schema import Timeline


def _now() -> datetime:
    return datetime.now(UTC)


def redo_target(current: int, links: list[tuple[int, int | None]]) -> int | None:
    """Newest version whose parent is ``current`` (the branch made most recently)."""
    children = [v for v, parent in links if parent == current]
    return max(children) if children else None


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
        """Move pointer to the current version's parent (no-op at the root)."""
        parent = self.parent_of(self.current)
        if parent is None:
            return self
        return VersionLog(entries=self.entries, current=parent)

    def redo(self) -> "VersionLog":
        """Move pointer to the newest child of the current version (no-op without one)."""
        target = redo_target(self.current, [(e.version, e.parent) for e in self.entries])
        if target is None:
            return self
        return VersionLog(entries=self.entries, current=target)

    def get(self, version: int) -> VersionEntry | None:
        """Fetch an entry by version number."""
        return next((e for e in self.entries if e.version == version), None)

    def parent_of(self, version: int) -> int | None:
        """Parent version number (None for v1)."""
        entry = self.get(version)
        return entry.parent if entry else None


async def save_version(
    db: Database, timeline_id: uuid.UUID, tl: Timeline, parent: int | None
) -> tuple[int, Timeline]:
    """Persist an EDL snapshot stamped with its version; bump the current pointer.

    The unique (timeline_id, version) index makes concurrent saves safe: a loser
    retries with the next number.
    """
    if await db_ops.get(db, TimelineDoc, timeline_id) is None:
        raise ValueError(f"timeline {timeline_id} does not exist")
    for _ in range(5):
        last = await db_ops.find_one(
            db, TimelineVersionDoc, {"timeline_id": timeline_id}, sort=[("version", -1)]
        )
        version = (last.version if last else 0) + 1
        stamped = tl.model_copy(update={"version": version})
        try:
            await db_ops.insert(
                db,
                TimelineVersionDoc(
                    timeline_id=timeline_id,
                    version=version,
                    edl=stamped.model_dump(mode="json"),
                    parent_version=parent,
                ),
            )
        except DuplicateKeyError:
            continue
        await db_ops.update(
            db, TimelineDoc, timeline_id, {"current_version": version, "updated_at": _now()}
        )
        return (version, stamped)
    raise RuntimeError(f"timeline {timeline_id}: could not allocate a version number")


async def load_version(db: Database, timeline_id: uuid.UUID, version: int) -> Timeline:
    """Load one snapshot as a Timeline."""
    row = await db_ops.find_one(
        db, TimelineVersionDoc, {"timeline_id": timeline_id, "version": version}
    )
    if row is None:
        raise ValueError(f"timeline {timeline_id} has no version {version}")
    return Timeline.model_validate(row.edl)


async def list_versions(db: Database, timeline_id: uuid.UUID) -> list[VersionEntry]:
    """All snapshots for a timeline, oldest first."""
    rows = await db_ops.find(
        db, TimelineVersionDoc, {"timeline_id": timeline_id}, sort=[("version", 1)]
    )
    return [VersionEntry(version=r.version, parent=r.parent_version, edl=dict(r.edl)) for r in rows]


async def get_current(db: Database, timeline_id: uuid.UUID) -> int:
    """Current version pointer."""
    row = await db_ops.get(db, TimelineDoc, timeline_id)
    if row is None:
        raise ValueError(f"timeline {timeline_id} does not exist")
    return row.current_version


async def set_current(db: Database, timeline_id: uuid.UUID, version: int) -> None:
    """Move the pointer (undo/redo) without deleting history."""
    exists = await db_ops.find_one(
        db, TimelineVersionDoc, {"timeline_id": timeline_id, "version": version}
    )
    if exists is None:
        raise ValueError(f"timeline {timeline_id} has no version {version}")
    await db_ops.update(
        db, TimelineDoc, timeline_id, {"current_version": version, "updated_at": _now()}
    )


async def undo(db: Database, timeline_id: uuid.UUID) -> int:
    """Move the pointer to the current version's parent; return the version now current."""
    current = await get_current(db, timeline_id)
    entry = await db_ops.find_one(
        db, TimelineVersionDoc, {"timeline_id": timeline_id, "version": current}
    )
    target = entry.parent_version if entry and entry.parent_version else current
    if target != current:
        await set_current(db, timeline_id, target)
    return target


async def redo(db: Database, timeline_id: uuid.UUID) -> int:
    """Move the pointer to the newest child of the current version; return it."""
    current = await get_current(db, timeline_id)
    links = [(e.version, e.parent) for e in await list_versions(db, timeline_id)]
    target = redo_target(current, links) or current
    if target != current:
        await set_current(db, timeline_id, target)
    return target
