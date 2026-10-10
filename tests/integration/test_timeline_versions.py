"""Version persistence integration test against compose Postgres. Opt-in."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import create_engine, session_factory
from app.models import Project, User
from app.models import Timeline as TimelineRow
from app.settings import get_settings
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


async def _seed(session: AsyncSession) -> uuid.UUID:
    user = User(api_key_hash="itest-key")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, name="itest")
    session.add(project)
    await session.flush()
    row = TimelineRow(project_id=project.id, current_version=0)
    session.add(row)
    await session.flush()
    return row.id


async def test_save_load_undo_branch_round_trip() -> None:
    engine = create_engine(get_settings())
    try:
        factory = session_factory(engine)
        async with factory() as session:
            tid = await _seed(session)
            v1, stamped1 = await save_version(session, tid, _tl(), parent=None)
            v2, stamped2 = await save_version(session, tid, _tl(), parent=v1)
            assert (v1, v2) == (1, 2)
            assert (stamped1.version, stamped2.version) == (1, 2)
            assert await get_current(session, tid) == 2

            await set_current(session, tid, 1)  # undo = pointer move
            assert await get_current(session, tid) == 1

            v3, stamped3 = await save_version(session, tid, _tl(), parent=1)  # branch
            assert v3 == 3
            assert stamped3.version == 3
            entries = await list_versions(session, tid)
            assert [e.version for e in entries] == [1, 2, 3]
            assert entries[2].parent == 1

            loaded = await load_version(session, tid, 2)
            assert loaded.duration_s == _tl().duration_s
            await session.rollback()  # leave no test rows behind
    finally:
        await engine.dispose()
