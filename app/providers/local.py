"""Local asset-library providers (ADR-014): music, images, GIFs, meme templates.

Libraries live under ASSETS_DIR/<library>/ with a manifest.json written by
scripts/fetch_assets.py. Every entry must carry license metadata (rule 7); entries
without it are dropped at load. Results use ``asset://<library>/<file>`` URLs that
resolve only inside ASSETS_DIR. No network access happens here.
"""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
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
    aliases: list[str] = []  # spoken names that mean exactly this asset (brand icons)


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


SYNONYMS: dict[str, set[str]] = {
    "coding": {
        "code",
        "software",
        "developer",
        "programming",
        "ide",
        "terminal",
        "typing",
        "matrix",
        "dev",
        "tech",
    },
    "code": {
        "coding",
        "software",
        "developer",
        "programming",
        "ide",
        "terminal",
        "matrix",
        "dev",
        "tech",
    },
    "developer": {
        "coding",
        "code",
        "software",
        "programming",
        "ide",
        "engineer",
        "builder",
        "dev",
        "tech",
    },
    "programmer": {"coding", "code", "software", "developer", "programming", "ide"},
    "software": {"code", "coding", "developer", "programming", "app", "application", "tech"},
    "ai": {
        "artificial",
        "intelligence",
        "neural",
        "brain",
        "machine",
        "learning",
        "robot",
        "smart",
        "claude",
        "llm",
        "future",
    },
    "claude": {
        "ai",
        "code",
        "coding",
        "developer",
        "programming",
        "robot",
        "software",
        "neural",
        "terminal",
    },
    "builder": {"developer", "code", "startup", "office", "engineer", "build", "production"},
    "production": {
        "datacenter",
        "server",
        "architecture",
        "cloud",
        "aws",
        "network",
        "system",
        "code",
    },
    "system": {
        "datacenter",
        "server",
        "architecture",
        "cloud",
        "network",
        "microprocessor",
        "chip",
    },
    "dse": {"coding", "code", "programming", "languages", "developer", "software", "matrix"},
    "dsa": {"coding", "code", "programming", "languages", "developer", "matrix"},
    "startup": {"office", "builder", "business", "desk", "laptop", "teamwork", "growth"},
    "tech": {
        "technology",
        "computer",
        "digital",
        "matrix",
        "cyber",
        "hardware",
        "software",
        "data",
    },
    "cloud": {"server", "datacenter", "amazon", "aws", "network", "infrastructure"},
    "money": {"finance", "stock", "market", "wealth", "cash", "dollars", "profit", "crypto"},
    "finance": {"money", "stock", "market", "wealth", "profit", "trading", "chart"},
    "growth": {"stock", "market", "chart", "profit", "success", "wealth"},
}


def _expand_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for t in tokens:
        if t in SYNONYMS:
            expanded.update(SYNONYMS[t])
    return expanded


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


# Tags that describe the medium, not the content: they never make an asset "relevant".
GENERIC_TAGS = frozenset({
    "video", "videos", "b", "roll", "broll", "clip", "visual", "image", "photo", "picture",
    "stock", "logo", "icon", "brand", "meme", "gif", "sticker", "background", "free",
})  # fmt: skip


def _stem(token: str) -> str:
    """Light plural folding so 'gpus' meets 'gpu' and 'servers' meets 'server'."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def content_tokens(text: str) -> set[str]:
    """Stemmed, non-generic tokens of a tag/title/spoken phrase."""
    return {_stem(t) for t in _WORD.findall(text.lower())} - GENERIC_TAGS


def alias_key(text: str) -> str:
    """'Chat GPT' / 'chatgpt' / 'ChatGPT' -> 'chatgpt' ('c++' keeps its pluses)."""
    return re.sub(r"[^a-z0-9+]", "", text.lower())


class Library:
    """A validated manifest plus weighted tag/title/synonym search."""

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
        """Items ranked by direct and synonym matches with tags and title."""
        wanted_direct = _tokens(query)
        wanted_expanded = _expand_tokens(wanted_direct)
        pool = [i for i in self.items if all(getattr(i, k) == v for k, v in (where or {}).items())]

        scored = []
        for n, i in enumerate(pool):
            tags_tokens = _tokens(" ".join(i.tags))
            title_tokens = _tokens(i.title)
            # Weighted: direct tag hit 3, direct title hit 2, synonym hit 1
            direct_tag_hits = len(wanted_direct & tags_tokens)
            direct_title_hits = len(wanted_direct & title_tokens)
            synonym_hits = len((wanted_expanded - wanted_direct) & (tags_tokens | title_tokens))
            score = (direct_tag_hits * 3) + (direct_title_hits * 2) + synonym_hits
            scored.append((score, n, i))

        hits = scored if where or not wanted_direct else [s for s in scored if s[0] > 0]
        hits.sort(key=lambda s: (-s[0], s[1]))
        return [i for _, _, i in hits[:limit]]

    @cached_property
    def _match_index(
        self,
    ) -> tuple[Counter[str], list[tuple[LibraryItem, set[str], set[str], set[str]]]]:
        rows = [
            (
                i,
                content_tokens(" ".join(i.tags) + " " + i.title),
                {alias_key(a) for a in i.aliases},
                content_tokens(" ".join(i.tags)),
            )
            for i in self.items
        ]
        df: Counter[str] = Counter(t for _, toks, _, _ in rows for t in toks)
        return df, rows

    def match(self, phrase: str) -> list[tuple[float, LibraryItem]]:
        """Relevance-scored items for a spoken phrase, best first.

        An exact alias ('chatgpt' for the OpenAI icon) scores 10. Otherwise EVERY content
        token of the phrase must be a tag/title token of the item; the score is 2..5 by
        how specific those tags are (normalised IDF), so a tag shared by half the library
        scores low whatever the library size. Generic medium words never count.
        """
        df, rows = self._match_index
        key = alias_key(phrase)
        wanted = content_tokens(phrase)
        n = max(1, len(rows))
        out: list[tuple[float, LibraryItem]] = []
        for item, toks, aliases, tag_toks in rows:
            if key and key in aliases:
                out.append((10.0, item))
            elif wanted and wanted <= toks:
                # specificity 0..1: 1 = tag unique to this item, ~0 = tag on everything
                spec = sum(math.log(1 + n / df[t]) for t in wanted) / (
                    len(wanted) * math.log(1 + n)
                )
                # titles carry incidental words ("server room taking shape"): tags count more
                weight = 1.0 if wanted <= tag_toks else 0.6
                phrase_bonus = 1 + 0.25 * (len(wanted) - 1)
                out.append((round((2 + 3 * spec) * weight * phrase_bonus, 3), item))
        out.sort(key=lambda x: -x[0])
        return out

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


def _parse_color(val: str | None, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not val:
        return default
    s = val.lstrip("#")
    if len(s) == 6:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        return (r, g, b, default[3])
    elif len(s) == 8:
        r, g, b, a = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16)
        return (r, g, b, a)
    return default


def _draw_headline_card(
    dest: Path,
    text: str,
    style: str = "banner",
    bg_color: str | None = None,
    text_color: str | None = None,
) -> None:
    import textwrap

    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    from app.render.fonts import font_for

    is_dev = any("DEVANAGARI" in unicodedata.name(ch, "") for ch in text)
    _, font_path = font_for("devanagari" if is_dev else "latin_bold")

    clean_text = _clean(text).strip()
    if not clean_text:
        clean_text = "HEADLINE"

    scale = 2
    font_size = 48 * scale
    font = ImageFont.truetype(font_path, font_size)

    lines = textwrap.wrap(clean_text, width=22)
    if not lines:
        lines = [clean_text]

    test_img = Image.new("RGBA", (1200 * scale, 400 * scale), (0, 0, 0, 0))
    test_draw = ImageDraw.Draw(test_img)

    line_heights: list[int] = []
    max_line_w = 0
    for line in lines:
        bbox = test_draw.textbbox((0, 0), line, font=font)
        lw = int(bbox[2] - bbox[0])
        lh = int(bbox[3] - bbox[1])
        max_line_w = max(max_line_w, lw)
        line_heights.append(lh)

    line_gap = 12 * scale
    total_text_h = sum(line_heights) + (len(lines) - 1) * line_gap
    pad_x = 48 * scale
    pad_y = 24 * scale

    box_w = min(960 * scale, max(max_line_w + pad_x * 2, 520 * scale))
    box_h = total_text_h + pad_y * 2
    radius = box_h // 2 if style == "pill" else 20 * scale

    shadow_margin = 22 * scale
    canvas_w = box_w + shadow_margin * 2
    canvas_h = box_h + shadow_margin * 2

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    # 1. Soft Ambient Elevation Drop Shadow
    shadow_img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow_img)
    s_offset_y = 6 * scale
    s_box = [
        shadow_margin,
        shadow_margin + s_offset_y,
        shadow_margin + box_w - 1,
        shadow_margin + box_h + s_offset_y - 1,
    ]
    s_draw.rounded_rectangle(s_box, radius=radius, fill=(0, 0, 0, 150))
    shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=10 * scale))
    canvas.alpha_composite(shadow_img)

    # 2. Card Surface with Gradient
    card_img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(card_img)

    if bg_color:
        base_rgb = _parse_color(bg_color, (17, 24, 39, 240))
        for y in range(box_h):
            factor = y / max(1, box_h - 1)
            r = max(0, int(base_rgb[0] * (1.1 - factor * 0.25)))
            g = max(0, int(base_rgb[1] * (1.1 - factor * 0.25)))
            b = max(0, int(base_rgb[2] * (1.1 - factor * 0.25)))
            c_draw.line([(0, y), (box_w, y)], fill=(r, g, b, base_rgb[3]))
    elif style == "alert":
        for y in range(box_h):
            factor = y / max(1, box_h - 1)
            r = int(160 * (1 - factor) + 80 * factor)
            g = int(24 * (1 - factor) + 12 * factor)
            b = int(30 * (1 - factor) + 16 * factor)
            c_draw.line([(0, y), (box_w, y)], fill=(r, g, b, 245))
    elif style == "pill":
        for y in range(box_h):
            factor = y / max(1, box_h - 1)
            r = int(22 * (1 - factor) + 10 * factor)
            g = int(24 * (1 - factor) + 12 * factor)
            b = int(32 * (1 - factor) + 16 * factor)
            c_draw.line([(0, y), (box_w, y)], fill=(r, g, b, 240))
    else:  # banner / minimal
        for y in range(box_h):
            factor = y / max(1, box_h - 1)
            r = int(28 * (1 - factor) + 12 * factor)
            g = int(32 * (1 - factor) + 15 * factor)
            b = int(44 * (1 - factor) + 22 * factor)
            c_draw.line([(0, y), (box_w, y)], fill=(r, g, b, 242))

    mask = Image.new("L", (box_w, box_h), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=radius, fill=255)
    card_composite = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    card_composite.paste(card_img, (0, 0), mask=mask)

    # 3. Outer Subtle Glass Border
    card_draw = ImageDraw.Draw(card_composite)
    border_color = (254, 202, 202, 100) if style == "alert" else (255, 255, 255, 45)
    card_draw.rounded_rectangle(
        [0, 0, box_w - 1, box_h - 1],
        radius=radius,
        fill=None,
        outline=border_color,
        width=2 * scale,
    )

    # 4. Text with subtle shadow
    fg_rgba = _parse_color(text_color, (255, 255, 255, 255))
    curr_y = pad_y
    for i, line in enumerate(lines):
        bbox = card_draw.textbbox((0, 0), line, font=font)
        lw = int(bbox[2] - bbox[0])
        x = (box_w - lw) // 2
        card_draw.text((x, curr_y + 2 * scale), line, font=font, fill=(0, 0, 0, 160))
        card_draw.text((x, curr_y), line, font=font, fill=fg_rgba)
        curr_y += line_heights[i] + line_gap

    canvas.alpha_composite(card_composite, (shadow_margin, shadow_margin))

    # Downscale with high quality Lanczos filter for smooth antialiased edges
    final_img = canvas.resize((canvas_w // scale, canvas_h // scale), Image.Resampling.LANCZOS)
    final_img.save(dest, "PNG")


LIBRARIES = (
    "images", "gifs", "videos", "icons", "memes", "music", "sfx", "generated", "headlines",
)  # fmt: skip


def resolve_asset_file(assets_root: str | Path, meme_dir: str | Path, asset_id: str) -> Path | None:
    """File for a library asset id (``images:<id>`` ...) or a rendered meme
    (``memes:<template>:<digest>``) or rendered headline (``headlines:<digest>``);
    None when it is not a library asset."""
    lib_name = asset_id.split(":", 1)[0]
    if lib_name not in LIBRARIES:
        return None
    if lib_name == "memes" and asset_id.count(":") == 2:
        digest = asset_id.rsplit(":", 1)[1]
        if not re.fullmatch(r"[0-9a-f]{16}", digest):
            return None
        path = Path(meme_dir) / f"{digest}.png"
        return path if path.is_file() else None
    if lib_name == "headlines" and ":" in asset_id:
        digest = asset_id.split(":", 1)[1]
        if not re.fullmatch(r"[0-9a-f]{16}", digest):
            return None
        path = Path(meme_dir) / f"hl_{digest}.png"
        return path if path.is_file() else None
    lib = Library(assets_root, lib_name)
    item = next((i for i in lib.items if i.id == asset_id), None)
    return lib.path(item) if item else None


class AssetCatalog:
    """Synchronous facade over the local libraries for the tool executor."""

    KINDS = {
        "image": "images", "gif": "gifs", "video": "videos", "meme": "memes", "music": "music",
        "sfx": "sfx", "icon": "icons",
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

    def match(self, kind: str, phrase: str) -> list[tuple[float, LibraryItem]]:
        """Relevance-scored matches for a spoken phrase in one library (see Library.match)."""
        return self._libs[kind].match(phrase)

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

    def render_headline(
        self,
        text: str,
        style: str = "banner",
        bg_color: str | None = None,
        text_color: str | None = None,
    ) -> tuple[str, LibraryItem]:
        """Render a headline card PNG; returns (rendered asset id, item)."""
        text_c = _clean(text).strip()
        style_c = (style or "banner").strip()
        bg_c = (bg_color or "").strip().lower()
        fg_c = (text_color or "").strip().lower()
        raw_key = f"{text_c}\x00{style_c}\x00{bg_c}\x00{fg_c}"
        digest = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
        out = self.meme_dir / f"hl_{digest}.png"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            _draw_headline_card(
                out, text_c, style=style_c, bg_color=bg_c or None, text_color=fg_c or None
            )
        item = LibraryItem(
            id=f"headlines:{digest}",
            file=f"hl_{digest}.png",
            title=f"Headline: {text_c[:30]}",
            tags=["headline", "card", style_c],
            source="generated",
            license="proprietary",
            attribution_required=False,
            attribution_text="",
            commercial_use=True,
            alpha=True,
        )
        return f"headlines:{digest}", item
