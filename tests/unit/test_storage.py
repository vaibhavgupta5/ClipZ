"""Storage unit tests: MemoryStorage + LocalStorage routing, traversal and lifecycle."""

from pathlib import Path

import pytest

from app.storage import LocalStorage, MemoryStorage, Storage, validate_key


def test_memory_storage_round_trip() -> None:
    store: Storage = MemoryStorage()
    store.put_bytes("proj/a.bin", b"hello", "application/octet-stream")
    assert store.get_bytes("proj/a.bin") == b"hello"
    store.delete("proj/a.bin")
    with pytest.raises(KeyError):
        store.get_bytes("proj/a.bin")


def test_validate_key_rejects_traversal() -> None:
    for bad in ["", "../etc/passwd", "a/../../b", "/absolute", "x" * 1025, "a\\b", "a\x00b"]:
        with pytest.raises(ValueError):
            validate_key(bad)
    validate_key("projects/p1/master.mp4")  # must not raise


def _store(tmp_path: Path) -> LocalStorage:
    s = LocalStorage(str(tmp_path / "upload"), str(tmp_path / "work"), str(tmp_path / "edited"))
    s.ensure_dirs()
    return s


def test_keys_route_to_the_three_roots(tmp_path: Path) -> None:
    s = _store(tmp_path)
    assert s.path_for("uploads/p1/x/raw.mp4") == tmp_path / "upload/p1/x/raw.mp4"
    assert s.path_for("renders/p1/final-ab.mp4") == tmp_path / "edited/p1/final-ab.mp4"
    assert s.path_for("projects/p1/master.mp4") == tmp_path / "work/p1/master.mp4"
    assert s.path_for("segments/abc.mp4") == tmp_path / "work/cache/segments/abc.mp4"
    with pytest.raises(ValueError):
        s.path_for("uploads")  # no object name


def test_local_round_trip_stat_delete(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.put_bytes("projects/p1/a.bin", b"abc", "application/octet-stream")
    assert s.get_bytes("projects/p1/a.bin") == b"abc" and s.stat("projects/p1/a.bin") == 3
    assert s.public_url("renders/p1/x.mp4") == "/media/renders/p1/x.mp4"
    s.delete("projects/p1/a.bin")
    with pytest.raises(KeyError):
        s.stat("projects/p1/a.bin")
    assert s.writable()


def test_delete_tree_is_scoped(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.put_bytes("projects/p1/master.mp4", b"m", "video/mp4")
    s.put_bytes("renders/p1/final.mp4", b"f", "video/mp4")
    s.delete_tree("projects/p1")
    assert not (tmp_path / "work/p1").exists()
    assert (tmp_path / "edited/p1/final.mp4").exists()  # exports are kept
    for bad in ("renders/p1", "projects", "projects/p1/sub", "segments/x"):
        with pytest.raises(ValueError):
            s.delete_tree(bad)
