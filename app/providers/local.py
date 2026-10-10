"""Local asset-library providers (ADR-014): music, images, GIFs, meme templates.

Libraries live under ASSETS_DIR/<library>/ with a manifest.json written by
scripts/fetch_assets.py. Every entry must carry license metadata (rule 7); entries
without it are dropped at load. Results use ``asset://<library>/<file>`` URLs that
resolve only inside ASSETS_DIR. No network access happens here.
"""

import hashlib
import json
import re
import unicodedata
from functools import cached_property
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.logging_setup import get_logger
from app.providers.base import (
    Candidate,
    MemeTemplate,
    MusicSearchQuery,
    MusicTrack,
    VisualQuery,
)

log = get_logger(component="assets")

_WORD = re.compile(r"[a-z0-9]+")


class LibraryItem(BaseModel):
    """One manifest entry. License fields are mandatory."""

    id: str
    file: str
    title: str
    tags: list[str]
    source: str
    source_url: str | None = None
    license: str
    license_url: str | None = None
    attribution_required: bool
    attribution_text: str = ""
    commercial_use: bool
    genre: str = ""
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    text_boxes: int = 2
    alpha: bool = False  # transparent sticker (overlay without a box)
    sfx_type: str = ""  # whoosh, pop, ding... (sound effects library)
    category: str = ""  # tech, comedy, info... (images)


def asset_url(library: str, file: str) -> str:
    """URL for a library file."""
    return f"asset://{library}/{file}"


def asset_path(root: str | Path, url: str) -> Path:
    """Resolve an ``asset://`` URL to a file inside ``root``; raises on anything else."""
    if not url.startswith("asset://"):
        raise ValueError(f"not an asset url: {url!r}")
    base = Path(root).resolve()
    path = (base / url.removeprefix("asset://")).resolve()
    if not path.is_relative_to(base) or ".." in url:
        raise ValueError(f"asset url escapes the library: {url!r}")
    return path


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


class Library:
    """A validated manifest plus simple tag/title search."""

    def __init__(self, root: str | Path, name: str) -> None:
        self.root = Path(root)
        self.name = name

    @cached_property
    def items(self) -> list[LibraryItem]:
        """Valid entries whose files exist (loaded once)."""
        manifest = self.root / self.name / "manifest.json"
        if not manifest.exists():
            return []
        valid: list[LibraryItem] = []
        for raw in json.loads(manifest.read_text(encoding="utf-8")):
            try:
                item = LibraryItem.model_validate(raw)
            except ValidationError as exc:
                log.warning(
                    "dropping asset without required metadata", library=self.name, error=str(exc)
                )
                continue
            if not item.license or not (self.root / self.name / item.file).is_file():
                continue
            valid.append(item)
        return valid

    def search(
        self, query: str, limit: int, where: dict[str, str] | None = None
    ) -> list[LibraryItem]:
        """Items ranked by query-token overlap with tags + title (ties keep manifest order).

        With ``where`` every item passing the filter is returned, best matches first.
        """
        wanted = _tokens(query)
        pool = [i for i in self.items if all(getattr(i, k) == v for k, v in (where or {}).items())]
        scored = [
            (len(wanted & (_tokens(" ".join(i.tags)) | _tokens(i.title))), n, i)
            for n, i in enumerate(pool)
        ]
        # A filter (e.g. genre) narrows the pool and the query only ranks within it;
        # without a filter, only items that match the query are returned.
        hits = scored if where or not wanted else [s for s in scored if s[0] > 0]
        hits.sort(key=lambda s: (-s[0], s[1]))
        return [i for _, _, i in hits[:limit]]

    def path(self, item: LibraryItem) -> Path:
        """Absolute file path for an item."""
        return asset_path(self.root, asset_url(self.name, item.file))


def _candidate(lib: Library, item: LibraryItem, kind: str) -> Candidate:
    return Candidate(
        id=item.id,
        kind=kind,  # type: ignore[arg-type]
        source=item.source,
        url=asset_url(lib.name, item.file),
        thumb_url=asset_url(lib.name, item.file),
        width=item.width,
        height=item.height,
        license=item.license,
        attribution_required=item.attribution_required,
        attribution_text=item.attribution_text,
        commercial_use=item.commercial_use,
        tags=item.tags,
    )


class LocalMusicProvider:
    """Royalty-free music beds by genre from ASSETS_DIR/music."""

    provider_name = "local-music"

    def __init__(self, root: str | Path) -> None:
        self.lib = Library(root, "music")

    def genres(self) -> list[str]:
        """Genre labels present in the library."""
        return sorted({i.genre for i in self.lib.items if i.genre})

    async def search(self, query: MusicSearchQuery) -> list[MusicTrack]:
        """Tracks for a genre and/or mood; long-enough tracks first (beds loop otherwise)."""
        where = {"genre": query.genre} if query.genre else None
        items = self.lib.search(query.mood, limit=20, where=where)
        items.sort(key=lambda i: i.duration_s < query.duration_s)  # stable: keeps relevance order
        return [
            MusicTrack(
                id=i.id,
                title=i.title,
                url=asset_url("music", i.file),
                duration_s=i.duration_s,
                license=i.license,
                attribution_required=i.attribution_required,
                attribution_text=i.attribution_text,
                genre=i.genre,
                commercial_use=i.commercial_use,
            )
            for i in items[:5]
        ]

    async def download(self, track_id: str) -> bytes:
        """Track bytes from disk."""
        item = next((i for i in self.lib.items if i.id == track_id), None)
        if item is None:
            raise KeyError(f"unknown track {track_id!r}")
        return self.lib.path(item).read_bytes()


class LocalImageProvider:
    """Stock photos from ASSETS_DIR/images."""

    provider_name = "local-images"

    def __init__(self, root: str | Path) -> None:
        self.lib = Library(root, "images")

    async def search(self, query: VisualQuery) -> list[Candidate]:
        """Best-matching photos."""
        return [_candidate(self.lib, i, "image") for i in self.lib.search(query.query, query.count)]


class LocalGifProvider:
    """Animated reactions (stored as silent mp4) from ASSETS_DIR/gifs."""

    provider_name = "local-gifs"

    def __init__(self, root: str | Path) -> None:
        self.lib = Library(root, "gifs")

    async def search(self, query: VisualQuery) -> list[Candidate]:
        """Best-matching animations."""
        return [_candidate(self.lib, i, "gif") for i in self.lib.search(query.query, query.count)]


def _clean(text: str, limit: int = 80) -> str:
    """Printable, single-line, length-capped caption text."""
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())[:limit].upper()


class LocalMemeTemplateProvider:
    """Popular meme templates (personal use only) + Pillow-rendered captions."""

    provider_name = "local-memes"

    def __init__(self, root: str | Path, out_dir: str | Path) -> None:
        self.lib = Library(root, "memes")
        self.out_dir = Path(out_dir)

    async def list_templates(self, limit: int = 50) -> list[MemeTemplate]:
        """Templates in manifest (popularity) order."""
        return [
            MemeTemplate(
                id=i.id,
                name=i.title,
                url=asset_url("memes", i.file),
                license=i.license,
                attribution_required=i.attribution_required,
                commercial_use=i.commercial_use,
                text_boxes=i.text_boxes,
            )
            for i in self.lib.items[:limit]
        ]

    async def render_captioned(
        self, template_id: str, top_text: str, bottom_text: str
    ) -> Candidate:
        """Draw outlined top/bottom text onto a template; cached by content hash."""
        item = next((i for i in self.lib.items if i.id == template_id), None)
        if item is None:
            raise KeyError(f"unknown meme template {template_id!r}")
        top, bottom = _clean(top_text), _clean(bottom_text)
        digest = hashlib.sha256(f"{template_id}\x00{top}\x00{bottom}".encode()).hexdigest()[:16]
        out = self.out_dir / f"{digest}.png"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            _draw_meme(self.lib.path(item), out, top, bottom)
        candidate = _candidate(self.lib, item, "meme")
        return candidate.model_copy(
            update={"id": f"{item.id}:{digest}", "url": str(out), "thumb_url": str(out)}
        )


def _draw_meme(src: Path, dest: Path, top: str, bottom: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    from app.render.fonts import font_for

    _, font_path = font_for("latin_bold")
    with Image.open(src) as opened:
        img = opened.convert("RGB")
    draw = ImageDraw.Draw(img)
    width, height = img.size
    for text, at_top in ((top, True), (bottom, False)):
        if not text:
            continue
        size = max(16, width // 10)
        font = ImageFont.truetype(font_path, size)
        while size > 16 and draw.textlength(text, font=font) > width * 0.94:
            size -= 2
            font = ImageFont.truetype(font_path, size)
        x = width / 2
        y = height * 0.03 if at_top else height * 0.97
        draw.text(
            (x, y),
            text,
            font=font,
            fill="white",
            stroke_width=max(2, size // 12),
            stroke_fill="black",
            anchor="mt" if at_top else "mb",
        )
    img.save(dest, "PNG")


LIBRARIES = ("images", "gifs", "memes", "music", "sfx", "generated")


def resolve_asset_file(assets_root: str | Path, meme_dir: str | Path, asset_id: str) -> Path | None:
    """File for a library asset id (``images:<id>`` ...) or a rendered meme
    (``memes:<template>:<digest>``); None when it is not a library asset."""
    lib_name = asset_id.split(":", 1)[0]
    if lib_name not in LIBRARIES:
        return None
    if lib_name == "memes" and asset_id.count(":") == 2:
        digest = asset_id.rsplit(":", 1)[1]
        if not re.fullmatch(r"[0-9a-f]{16}", digest):
            return None
        path = Path(meme_dir) / f"{digest}.png"
        return path if path.is_file() else None
    lib = Library(assets_root, lib_name)
    item = next((i for i in lib.items if i.id == asset_id), None)
    return lib.path(item) if item else None


class AssetCatalog:
    """Synchronous facade over the local libraries for the tool executor."""

    KINDS = {
        "image": "images", "gif": "gifs", "meme": "memes", "music": "music", "sfx": "sfx",
        "generated": "generated",  # ElevenLabs output (app/providers/generated.py)
    }  # fmt: skip

    def __init__(self, assets_root: str | Path, meme_dir: str | Path) -> None:
        self.root = Path(assets_root)
        self.meme_dir = Path(meme_dir)
        self._libs = {k: Library(self.root, v) for k, v in self.KINDS.items()}

    def reload(self, kind: str) -> None:
        """Re-read one library's manifest (after a generated clip was added)."""
        self._libs[kind] = Library(self.root, self.KINDS[kind])

    def search(self, kind: str, query: str, limit: int) -> list[LibraryItem]:
        """Best matches in one library (kind: image | gif | meme)."""
        return self._libs[kind].search(query, limit)

    def music(self, genre: str | None, mood: str, min_s: float) -> list[LibraryItem]:
        """Tracks in a genre (or all), mood-ranked, long-enough first."""
        lib = self._libs["music"]
        where = {"genre": genre} if genre else None
        items = lib.search(mood, 20, where=where) if (mood or where) else list(lib.items)
        return sorted(items, key=lambda i: i.duration_s < min_s)

    def sfx(self, sfx_type: str, variant: int = 0) -> LibraryItem | None:
        """One sound of a type; ``variant`` rotates through the available takes."""
        takes = [i for i in self._libs["sfx"].items if i.sfx_type == sfx_type]
        return takes[variant % len(takes)] if takes else None

    def sfx_types(self) -> list[str]:
        """Sound-effect types available."""
        return sorted({i.sfx_type for i in self._libs["sfx"].items if i.sfx_type})

    def genres(self) -> list[str]:
        """Music genre labels available."""
        return sorted({i.genre for i in self._libs["music"].items if i.genre})

    def get(self, asset_id: str) -> tuple[str, LibraryItem] | None:
        """(kind, item) for a library asset id."""
        for kind, lib in self._libs.items():
            item = next((i for i in lib.items if i.id == asset_id), None)
            if item is not None:
                return kind, item
        return None

    def render_meme(self, template_id: str, top: str, bottom: str) -> tuple[str, LibraryItem]:
        """Caption a template; returns (rendered asset id, template item)."""
        found = self.get(template_id)
        if found is None or found[0] != "meme":
            raise KeyError(f"unknown meme template {template_id!r}")
        item = found[1]
        top_c, bottom_c = _clean(top), _clean(bottom)
        digest = hashlib.sha256(f"{template_id}\x00{top_c}\x00{bottom_c}".encode()).hexdigest()[:16]
        out = self.meme_dir / f"{digest}.png"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            _draw_meme(self._libs["meme"].path(item), out, top_c, bottom_c)
        return f"{template_id}:{digest}", item
