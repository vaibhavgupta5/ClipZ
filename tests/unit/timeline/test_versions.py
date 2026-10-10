"""Versioning tests: immutable snapshots, undo/redo pointers, branch on edit-after-undo."""

from app.timeline.schema import CaptionConfig, OutputSpec, Timeline, VideoClip
from app.timeline.transcript import Transcript, Word
from app.timeline.versions import VersionEntry, VersionLog


def _tl() -> Timeline:
    return Timeline(
        project_id="00000000-0000-0000-0000-000000000000",
        transcript=Transcript(
            words=[Word(id="w1", start=0.0, end=0.4, text="x", display_text="x", lang="en")]
        ),
        video_clips=[VideoClip(id="c1", asset="a1", src_in=0.0, src_out=1.0)],
        captions=CaptionConfig(),
        output=OutputSpec(),
    )


def test_append_undo_redo_round_trip() -> None:
    log = VersionLog().append(_tl()).append(_tl())
    assert log.current == 2
    assert log.undo().current == 1
    assert log.redo().current == 2
    assert log.get(1) is not None and log.get(2) is not None


def test_undo_at_first_version_stays() -> None:
    log = VersionLog().append(_tl())
    assert log.undo().current == 1


def test_redo_follows_linear_chain() -> None:
    log = VersionLog().append(_tl()).append(_tl())
    assert log.undo().redo().current == 2


def test_edit_after_undo_branches_keeping_history() -> None:
    log = VersionLog().append(_tl()).append(_tl())
    log = log.undo()  # pointer at 1, v2 kept
    branched = log.append(_tl())  # v3 with parent 1
    assert branched.current == 3
    assert branched.parent_of(3) == 1
    assert branched.get(2) is not None  # old redo chain preserved
    assert branched.redo().current == 3  # no redo after branch


def test_version_log_is_immutable() -> None:
    log = VersionLog().append(_tl())
    grown = log.append(_tl())
    assert len(log.entries) == 1  # original untouched
    assert len(grown.entries) == 2
    assert log.current == 1 and grown.current == 2


def test_redo_refuses_nonlinear_jump() -> None:
    edl = _tl().model_dump(mode="json")
    log = VersionLog(
        entries=(
            VersionEntry(version=1, parent=None, edl=edl),
            VersionEntry(version=2, parent=None, edl=edl),  # parent != pointer: not a redo
        ),
        current=1,
    )
    assert log.redo().current == 1


def test_undo_follows_parent_after_branch() -> None:
    from app.timeline.versions import VersionLog
    from tests.unit.timeline.test_ops import _timeline

    log = VersionLog().append(_timeline()).append(_timeline()).append(_timeline())  # v1<-v2<-v3
    log = log.undo().append(_timeline())  # v4 branches off v2
    assert log.current == 4 and log.parent_of(4) == 2
    assert log.undo().current == 2  # not the abandoned v3


def test_redo_follows_newest_branch() -> None:
    from app.timeline.versions import VersionLog
    from tests.unit.timeline.test_ops import _timeline

    log = VersionLog().append(_timeline()).append(_timeline()).append(_timeline())  # v1<-v2<-v3
    log = log.undo().append(_timeline())  # v4 branches off v2
    back = log.undo()
    assert back.current == 2 and back.redo().current == 4  # not the abandoned v3
