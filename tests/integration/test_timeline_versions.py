"""Version persistence integration test against compose MongoDB. Opt-in."""

import uuid

import pytest

from app import db as db_ops
from app.db import Database
from app.models import Project, User
from app.models import Timeline as TimelineRow
from app.timeline.schema import CaptionConfig, OutputSpec, Timeline, VideoClip
from app.timeline.transcript import Transcript, Word
from app.timeline.versions import (
    get_current,
    list_versions,
    load_version,
    save_version,
    set_current,
)

pytestmark = pytest.mark.integration


def _tl() -> Timeline:
    return Timeline(
        project_id=uuid.uuid4(),
        transcript=Transcript(
            words=[Word(id="w1", start=0.0, end=0.4, text="x", display_text="x", lang="en")]
        ),
        video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=1.0)],
        captions=CaptionConfig(),
        output=OutputSpec(),
    )


async def _seed(db: Database) -> uuid.UUID:
    user = await db_ops.insert(db, User(api_key_hash="itest-key"))
    project = await db_ops.insert(db, Project(user_id=user.id, name="itest"))
    row = await db_ops.insert(db, TimelineRow(project_id=project.id, current_version=0))
    return row.id


async def test_save_load_undo_branch_round_trip() -> None:
    db = db_ops.get_db()
    tid = await _seed(db)
    v1, stamped1 = await save_version(db, tid, _tl(), parent=None)
    v2, stamped2 = await save_version(db, tid, _tl(), parent=v1)
    assert (v1, v2) == (1, 2)
    assert (stamped1.version, stamped2.version) == (1, 2)
    assert await get_current(db, tid) == 2

    await set_current(db, tid, 1)  # undo = pointer move
    assert await get_current(db, tid) == 1

    v3, stamped3 = await save_version(db, tid, _tl(), parent=1)  # branch
    assert v3 == 3
    assert stamped3.version == 3
    entries = await list_versions(db, tid)
    assert [e.version for e in entries] == [1, 2, 3]
    assert entries[2].parent == 1

    loaded = await load_version(db, tid, 2)
    assert loaded.duration_s == _tl().duration_s

    missing = uuid.uuid4()
    with pytest.raises(ValueError, match="does not exist"):
        await save_version(db, missing, _tl(), parent=None)
    with pytest.raises(ValueError, match="has no version"):
        await load_version(db, tid, 99)
    with pytest.raises(ValueError, match="does not exist"):
        await get_current(db, missing)
    with pytest.raises(ValueError, match="has no version"):
        await set_current(db, tid, 99)


async def test_concurrent_saves_get_distinct_versions() -> None:
    import asyncio

    db = db_ops.get_db()
    tid = await _seed(db)
    results = await asyncio.gather(*(save_version(db, tid, _tl(), parent=None) for _ in range(5)))
    assert sorted(v for v, _ in results) == [1, 2, 3, 4, 5]
