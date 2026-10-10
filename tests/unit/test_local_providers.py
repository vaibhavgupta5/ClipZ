"""Local asset libraries: manifest validation, search, traversal guard, meme rendering."""

import json
from pathlib import Path

import pytest

from app.providers.base import (
    GifProvider,
    ImageProvider,
    MemeTemplateProvider,
    MusicProvider,
    MusicSearchQuery,
    VisualQuery,
)
from app.providers.local import (
    LocalGifProvider,
    LocalImageProvider,
    LocalMemeTemplateProvider,
    LocalMusicProvider,
    asset_path,
)


def _item(id_: str, file: str, tags: list[str], **kw: object) -> dict[str, object]:
    return {
        "id": id_,
        "file": file,
        "title": kw.pop("title", id_),
        "tags": tags,
        "source": "openverse/test",
        "license": "CC-BY-2.0",
        "attribution_required": True,
        "attribution_text": f"{id_} by someone, CC BY 2.0",
        "commercial_use": True,
        **kw,
    }


def _library(
    root: Path, name: str, entries: list[dict[str, object]], files: dict[str, bytes]
) -> None:
    lib = root / name
    for rel, data in files.items():
        (lib / rel).parent.mkdir(parents=True, exist_ok=True)
        (lib / rel).write_bytes(data)
    (lib / "manifest.json").write_text(json.dumps(entries))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    from PIL import Image

    r = tmp_path / "assets"
    _library(
        r,
        "music",
        [
            _item("music:a", "lofi/a.mp3", ["lofi", "chill"], genre="lofi", duration_s=20.0),
            _item("music:b", "lofi/b.mp3", ["lofi"], genre="lofi", duration_s=120.0),
            _item(
                "music:c",
                "cinematic/c.mp3",
                ["cinematic", "epic"],
                genre="cinematic",
                duration_s=90.0,
            ),
            {
                "id": "music:nolicense",
                "file": "lofi/a.mp3",
                "title": "x",
                "tags": ["lofi"],
                "source": "s",
            },
            _item("music:missing", "lofi/gone.mp3", ["lofi"], genre="lofi", duration_s=60.0),
        ],
        {"lofi/a.mp3": b"A", "lofi/b.mp3": b"B", "cinematic/c.mp3": b"C"},
    )
    _library(
        r,
        "images",
        [_item("images:1", "1.jpg", ["money", "cash"]), _item("images:2", "2.jpg", ["rocket"])],
        {"1.jpg": b"1", "2.jpg": b"2"},
    )
    _library(r, "gifs", [_item("gifs:1", "1.mp4", ["happy", "dance"])], {"1.mp4": b"g"})
    (r / "memes").mkdir(parents=True)
    Image.new("RGB", (400, 300), "gray").save(r / "memes" / "drake.jpg")
    (r / "memes" / "manifest.json").write_text(
        json.dumps(
            [
                _item(
                    "memes:imgflip-1",
                    "drake.jpg",
                    ["drake", "hotline"],
                    title="Drake Hotline Bling",
                    license="unknown",
                    attribution_required=False,
                    attribution_text="",
                    commercial_use=False,
                )
            ]
        )
    )
    return r


async def test_music_by_genre_prefers_long_enough_tracks(root: Path) -> None:
    music = LocalMusicProvider(root)
    assert isinstance(music, MusicProvider)
    assert music.genres() == ["cinematic", "lofi"]
    tracks = await music.search(MusicSearchQuery(mood="chill", genre="lofi", duration_s=60))
    assert [t.id for t in tracks] == ["music:b", "music:a"]  # 120 s bed before the 20 s one
    assert tracks[0].url == "asset://music/lofi/b.mp3" and tracks[0].attribution_required
    assert await music.download("music:b") == b"B"
    # entries without license metadata or without a file never surface (rule 7)
    assert {i.id for i in music.lib.items} == {"music:a", "music:b", "music:c"}


async def test_visual_search_ranks_by_tags(root: Path) -> None:
    images, gifs = LocalImageProvider(root), LocalGifProvider(root)
    assert isinstance(images, ImageProvider) and isinstance(gifs, GifProvider)
    found = await images.search(VisualQuery(query="burning cash money"))
    assert [c.id for c in found] == ["images:1"] and found[0].license == "CC-BY-2.0"
    assert await images.search(VisualQuery(query="submarine")) == []  # no match -> nothing
    assert (await gifs.search(VisualQuery(query="happy")))[0].kind == "gif"


def test_asset_path_stays_inside_root(root: Path) -> None:
    assert asset_path(root, "asset://images/1.jpg") == (root / "images/1.jpg").resolve()
    for bad in ("asset://../secrets", "asset://images/../../x", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            asset_path(root, bad)


async def test_meme_templates_are_personal_use_and_render_safely(
    root: Path, tmp_path: Path
) -> None:
    from PIL import Image

    memes = LocalMemeTemplateProvider(root, tmp_path / "out")
    assert isinstance(memes, MemeTemplateProvider)
    [tpl] = await memes.list_templates()
    assert tpl.commercial_use is False and tpl.license == "unknown"
    hostile = "when the build\nis green\x00 '; drop table {\\an8} " + "x" * 200
    card = await memes.render_captioned(tpl.id, hostile, "ship it")
    assert card.commercial_use is False
    with Image.open(card.url) as img:
        assert img.size == (400, 300)
    again = await memes.render_captioned(tpl.id, hostile, "ship it")
    assert again.url == card.url  # content-hash cache
    with pytest.raises(KeyError):
        await memes.render_captioned("memes:nope", "a", "b")
