"""One-time importer for the local asset libraries (ADR-014). Run: `make assets`.

Builds ./assets/{music,images,gifs,memes}/ with a manifest.json per library:

    music   ~40 tracks, 8 genres x 5, CC0/CC BY (Openverse -> Jamendo/Freesound/Wikimedia)
    images  ~40 photos, CC0/PDM/CC BY, >= 1000 px wide (Openverse -> Flickr/Wikimedia)
    gifs    ~40 animations, CC0/PDM/CC BY, >= 200 px, converted to silent mp4 (Openverse)
    memes   ~40 popular templates (Imgflip get_memes, no key) - license UNKNOWN,
            stored as personal-use-only (commercial_use=false), blocked from monetised exports

This is the ONLY code that touches the network for assets; the app and tests read the
manifests. Downloads are https-only, host-allowlisted on every redirect hop, size-capped,
content-type checked and decode-checked with ffprobe. Openverse's anonymous limit is
20 requests/min and 200/day; requests are paced at 3.5 s. Re-running skips items that
are already present.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

OPENVERSE = "https://api.openverse.org/v1"
IMGFLIP = "https://api.imgflip.com/get_memes"
SAFE_LICENSES = "cc0,pdm,by"  # no NC (monetised reels), no SA/ND (we adapt and recombine)
UA = "ReelForge-asset-importer/1.0 (one-time local library build)"

ALLOWED_HOSTS = {
    "api.openverse.org",
    "api.imgflip.com",
    "i.imgflip.com",
    "upload.wikimedia.org",
    "live.staticflickr.com",
    "farm1.staticflickr.com",
    "farm2.staticflickr.com",
    "farm3.staticflickr.com",
    "farm4.staticflickr.com",
    "farm5.staticflickr.com",
    "farm6.staticflickr.com",
    "farm7.staticflickr.com",
    "farm8.staticflickr.com",
    "farm9.staticflickr.com",
    "cdn.freesound.org",
    "prod-1.storage.jamendo.com",
    "googlefonts.github.io",
    "fonts.gstatic.com",
}
# On top of Openverse's mature=false: titles/tags containing these are never imported.
BLOCKLIST = {
    "nude", "naked", "nudity", "thong", "bikini", "lingerie", "underwear", "topless", "sex",
    "sexy", "porn", "erotic", "breast", "breasts", "nipple", "gore", "blood", "corpse", "dead",
    "nazi", "swastika", "hitler", "drug", "drugs", "cocaine", "weed", "gun", "guns", "rifle",
    "shooting", "suicide", "kill", "killing", "war", "weapon", "violence", "racist",
}  # fmt: skip


EXCLUSIONS_FILE = Path(__file__).with_name("asset_exclusions.json")


def exclusions() -> dict[str, dict[str, str]]:
    """Manually reviewed items that must never be imported: {id: {title, reason}}."""
    return json.loads(EXCLUSIONS_FILE.read_text()) if EXCLUSIONS_FILE.exists() else {}


def purge(root: Path, lib: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop excluded items from a manifest and delete their files."""
    banned = exclusions()
    kept = []
    for item in items:
        if item["id"] in banned:
            (root / lib / item["file"]).unlink(missing_ok=True)
            print(f"  purged {item['id']}: {banned[item['id']]['reason']}")
        else:
            kept.append(item)
    return kept


def is_blocked(*texts: str) -> bool:
    """True when any word of the given titles/tags is on the blocklist."""
    import re

    words = {w for t in texts for w in re.findall(r"[a-z]+", (t or "").lower())}
    return bool(words & BLOCKLIST)


MAX_BYTES = {"music": 25_000_000, "images": 15_000_000, "gifs": 15_000_000, "memes": 5_000_000}
CONTENT_PREFIX = {"music": "audio/", "images": "image/", "gifs": "image/gif", "memes": "image/"}

# Noto animated emoji (Google, CC BY 4.0): transparent reaction stickers.
NOTO_CATALOG = "https://googlefonts.github.io/noto-emoji-animation/data/api.json"
NOTO_GIF = "https://fonts.gstatic.com/s/e/notoemoji/latest/{code}/512.gif"
NOTO_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
NOTO_SOURCE = "https://googlefonts.github.io/noto-emoji-animation/"
NOTO_REACTIONS: dict[str, list[str]] = {
    "1f602": ["laugh", "lol", "funny", "joke"],
    "1f923": ["rofl", "laugh", "funny"],
    "1f631": ["shocked", "scream", "omg", "wow"],
    "1f92f": ["mind blown", "shocked", "wow", "insane"],
    "1f525": ["fire", "hot", "lit", "hype"],
    "1f44f": ["clap", "applause", "bravo"],
    "1f480": ["dead", "skull", "dying laughing"],
    "1f973": ["party", "celebrate", "celebration"],
    "1f62d": ["crying", "sad", "tears"],
    "1f60d": ["love", "heart eyes", "amazing"],
    "1f914": ["thinking", "hmm", "question"],
    "1f60e": ["cool", "sunglasses", "confident"],
    "1f644": ["eye roll", "annoyed", "sarcastic"],
    "1f62c": ["awkward", "cringe", "grimace"],
    "1f921": ["clown", "fool", "joke"],
    "1f4af": ["hundred", "perfect", "agree"],
    "1f680": ["rocket", "launch", "growth", "to the moon"],
    "1f4b8": ["money", "cash", "spending", "expensive"],
    "1f389": ["confetti", "celebrate", "congrats"],
    "1f440": ["eyes", "look", "watching", "suspicious"],
    "1f62e": ["surprised", "wow", "oh"],
    "1fae0": ["melting", "embarrassed", "awkward"],
    "1f633": ["flushed", "shocked", "embarrassed"],
    "1f972": ["smile tear", "bittersweet", "proud"],
    "1f621": ["angry", "mad", "rage"],
    "1f911": ["money face", "rich", "profit", "money"],
    "1f44d": ["thumbs up", "ok", "approve", "like"],
    "1f64f": ["please", "thanks", "pray", "grateful"],
    "1f4aa": ["strong", "muscle", "power"],
    "2728": ["sparkles", "magic", "new", "shine"],
    "1fae1": ["salute", "respect", "yes sir"],
    "1f91d": ["handshake", "deal", "agreement"],
    "1f634": ["sleeping", "boring", "tired"],
    "1f92b": ["shush", "secret", "quiet"],
}

# Sound effects: CC0 only (no credit lines for every pop). type -> queries.
SFX_TYPES: dict[str, list[str]] = {
    "whoosh": ["whoosh", "swoosh transition"],
    "swoosh": ["swoosh", "swish"],
    "pop": ["pop sound", "bubble pop"],
    "ding": ["ding", "bell ding"],
    "riser": ["riser", "tension riser"],
    "boom": ["boom impact", "cinematic hit"],
    "click": ["mouse click", "ui click"],
    "record_scratch": ["record scratch", "vinyl scratch"],
    "camera": ["camera shutter"],
    "typing": ["keyboard typing"],
    "notification": ["notification sound", "message alert"],
    "glitch": ["glitch", "digital glitch"],
}  # fmt: skip

MUSIC_GENRES: dict[str, list[str]] = {
    "lofi": ["lofi", "lo-fi chill", "chillhop", "lofi hip hop", "jazzy chill", "chill beat"],
    "upbeat": ["upbeat pop", "happy upbeat"],
    "cinematic": ["cinematic", "epic orchestral"],
    "electronic": ["electronic", "synthwave"],
    "hiphop": ["hip hop beat", "hip hop instrumental"],
    "acoustic": ["acoustic guitar", "folk acoustic"],
    "corporate": [
        "corporate motivational",
        "inspiring corporate",
        "corporate",
        "motivational",
        "business background",
        "uplifting",
    ],
    "ambient": ["ambient", "calm ambient"],
}
# Category -> queries. The category is stored on each item so the Director can search
# within "tech", "comedy", "info"... Two images per query, CC0/PDM/BY only.
IMAGE_CATEGORIES: dict[str, list[str]] = {
    "tech": ["computer code", "programming", "server room", "circuit board", "data center",
             "smartphone app", "startup office", "artificial intelligence", "keyboard typing"],
    "comedy": ["funny dog", "funny cat", "surprised face", "facepalm", "laughing people",
               "silly face", "confused person"],
    "info": ["classroom", "chalkboard", "bar chart", "world map", "microscope", "library books",
             "calculator", "newspaper"],
    "business": ["business meeting", "handshake", "office desk", "presentation", "skyscraper"],
    "finance": ["coins", "stock chart", "bank", "credit card", "piggy bank", "cash register"],
    "fitness": ["gym", "running", "yoga", "healthy food"],
    "food": ["pizza", "burger", "coffee cup", "street food"],
    "travel": ["airplane", "beach", "train station", "suitcase"],
    "gaming": ["video game controller", "arcade", "chess"],
    "science": ["telescope", "laboratory", "space nebula", "dna"],
    "motivation": ["mountain summit", "trophy", "finish line", "sunrise mountain"],
    "nature": ["forest", "ocean waves", "waterfall", "storm clouds"],
    "mood": ["rain window", "empty road", "lonely bench", "dark clouds"],
}  # fmt: skip
QUERY_CATEGORY = {q: c for c, qs in IMAGE_CATEGORIES.items() for q in qs}

IMAGE_QUERIES = [
    "money",
    "technology",
    "laptop",
    "smartphone",
    "rocket launch",
    "city skyline",
    "success",
    "teamwork",
    "stock market",
    "coffee",
    "sunrise",
    "crowd",
    "workout",
    "food",
    "travel",
    "robot",
    "books",
    "mountain",
    "office",
    "celebration",
    "birthday cake",
    "birthday candles",
    "balloons",
    "gift box",
    "birthday party",
]
GIF_QUERIES = [
    "happy",
    "dance",
    "cat",
    "dog",
    "explosion",
    "fireworks",
    "thumbs up",
    "laugh",
    "wow",
    "applause",
    "rocket",
    "money",
    "heart",
    "star",
    "loading",
    "confetti",
    "party",
    "wave",
    "clap",
    "fire",
    "smile",
    "animation",
    "cartoon",
    "funny",
    "emoji",
    "sparkle",
    "spinning",
    "bounce",
]


class Fetcher:
    """Allowlisted, size-capped HTTP client with Openverse pacing."""

    def __init__(self, pace_s: float) -> None:
        self.client = httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=False)
        self.pace_s = pace_s
        self._last = 0.0
        self._host_last: dict[str, float] = {}

    def _pace_host(self, url: str, gap_s: float = 1.5) -> None:
        """Be gentle with media hosts (Wikimedia rate-limits bursts with 429)."""
        host = urlparse(url).hostname or ""
        wait = gap_s - (time.monotonic() - self._host_last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._host_last[host] = time.monotonic()

    def _check(self, url: str) -> None:
        parts = urlparse(url)
        if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"blocked url (scheme/host not allowlisted): {url}")

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        self._check(url)
        if "openverse" in url:
            wait = self.pace_s - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        resp = self.client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def download(self, url: str, dest: Path, max_bytes: int, content_prefix: str) -> None:
        """Download with 429 back-off (honours Retry-After, up to 3 retries)."""
        for attempt in range(4):
            try:
                self._download_once(url, dest, max_bytes, content_prefix)
                return
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 429 or attempt == 3:
                    raise
                retry = exc.response.headers.get("retry-after", "")
                time.sleep(min(60.0, float(retry)) if retry.isdigit() else 10.0 * (attempt + 1))

    def _download_once(self, url: str, dest: Path, max_bytes: int, content_prefix: str) -> None:
        for _ in range(5):  # follow redirects manually so every hop is allowlisted
            self._check(url)
            self._pace_host(url)
            with self.client.stream("GET", url) as resp:
                if resp.is_redirect:
                    url = str(resp.next_request.url) if resp.next_request else ""
                    continue
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                if not ctype.startswith(content_prefix):
                    raise ValueError(f"unexpected content-type {ctype!r} for {url}")
                size = 0
                tmp = dest.with_name(dest.name + ".part")
                with tmp.open("wb") as out:
                    for chunk in resp.iter_bytes(1 << 16):
                        size += len(chunk)
                        if size > max_bytes:
                            tmp.unlink(missing_ok=True)
                            raise ValueError(f"over {max_bytes} bytes: {url}")
                        out.write(chunk)
                tmp.replace(dest)
                return
        raise ValueError(f"too many redirects: {url}")


def probe(path: Path) -> dict[str, Any]:
    """ffprobe facts; raises when the file does not decode."""
    out = subprocess.run(  # noqa: S603 - fixed argv, our own path
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    data = json.loads(out.stdout)
    if not data.get("streams"):
        raise ValueError(f"no decodable streams: {path}")
    return data


def gif_to_mp4(src: Path, dest: Path) -> None:
    """Silent h264 mp4 with even dimensions (overlay-ready)."""
    subprocess.run(  # noqa: S603 - fixed argv, our own paths
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-an",
            str(dest),
        ],
        check=True,
        timeout=120,
    )


def _entry(
    lib: str, item_id: str, file: str, r: dict[str, Any], tags: list[str], **extra: Any
) -> dict[str, Any]:
    license_code = r["license"]
    return {
        "id": f"{lib}:{item_id}",
        "file": file,
        "title": r.get("title") or item_id,
        "tags": tags,
        "source": f"openverse/{r.get('source', '')}",
        "source_url": r.get("foreign_landing_url") or r.get("url"),
        "license": f"CC-{license_code.upper()}-{r.get('license_version', '')}".rstrip("-"),
        "license_url": r.get("license_url"),
        "attribution_required": license_code == "by",
        "attribution_text": r.get("attribution", "") if license_code == "by" else "",
        "commercial_use": True,
        **extra,
    }


def _safe(r: dict[str, Any]) -> bool:
    tags = [t.get("name", "") for t in r.get("tags") or []]
    return not r.get("mature") and not is_blocked(r.get("title") or "", *tags)


def _openverse(f: Fetcher, kind: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    data = f.get_json(
        f"{OPENVERSE}/{kind}/", {"license": SAFE_LICENSES, "mature": "false", **params}
    )
    return [r for r in data.get("results", []) if _safe(r)]


def _load(manifest: Path) -> list[dict[str, Any]]:
    return json.loads(manifest.read_text()) if manifest.exists() else []


def build_music(f: Fetcher, root: Path, per_genre: int) -> list[dict[str, Any]]:
    lib = root / "music"
    items = _load(lib / "manifest.json")
    have = {i["id"] for i in items} | set(exclusions())
    for genre, queries in MUSIC_GENRES.items():
        got = sum(1 for i in items if i.get("genre") == genre)
        # first pass: tracks Openverse categorises as music; second: loops (often "sound")
        passes = [(q, {"category": "music"}) for q in queries] + [(q, {}) for q in queries]
        for q, extra in passes:
            if got >= per_genre:
                break
            params = {"q": q, "page_size": 20, **extra}
            for r in _openverse(f, "audio", params):
                if got >= per_genre:
                    break
                item_id = r["id"]
                # loops are made to repeat under a bed, so shorter ones are fine
                min_ms = 15_000 if "loop" in (r.get("title") or "").lower() else 30_000
                if f"music:{item_id}" in have or (r.get("duration") or 0) < min_ms:
                    continue
                dest = lib / genre / f"{item_id}.mp3"
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    f.download(r["url"], dest, MAX_BYTES["music"], CONTENT_PREFIX["music"])
                    facts = probe(dest)
                except Exception as exc:  # noqa: BLE001 - skip bad items, keep going
                    print(f"  skip music {item_id}: {exc}", file=sys.stderr)
                    dest.unlink(missing_ok=True)
                    continue
                items.append(
                    _entry(
                        "music",
                        item_id,
                        str(dest.relative_to(lib)),
                        r,
                        tags=[genre, *(r.get("genres") or [])],
                        genre=genre,
                        duration_s=round(float(facts["format"]["duration"]), 2),
                    )
                )
                have.add(f"music:{item_id}")
                got += 1
                print(f"  music/{genre}: {r.get('title')!r} ({r['license']})")
    return items


def build_visual(
    f: Fetcher, root: Path, lib_name: str, queries: list[str], per_query: int, min_side: int
) -> list[dict[str, Any]]:
    lib = root / lib_name
    lib.mkdir(parents=True, exist_ok=True)
    items = _load(lib / "manifest.json")
    have = {i["id"] for i in items} | set(exclusions())
    seen_urls = {i.get("source_url") for i in items}
    for q in queries:
        got = sum(1 for i in items if q in i["tags"])
        if got >= per_query:
            continue
        params: dict[str, Any] = {"q": q, "page_size": 20}
        if lib_name == "gifs":
            params["extension"] = "gif"
        for r in _openverse(f, "images", params):
            if got >= per_query:
                break
            item_id = r["id"]
            w, h = r.get("width") or 0, r.get("height") or 0
            if f"{lib_name}:{item_id}" in have or r.get("foreign_landing_url") in seen_urls:
                continue
            if min(w, h) < min_side if lib_name == "gifs" else w < min_side:
                continue
            ext = (
                "gif"
                if lib_name == "gifs"
                else (Path(urlparse(r["url"]).path).suffix.lstrip(".") or "jpg")
            )
            raw = lib / f"{item_id}.{ext}"
            try:
                f.download(r["url"], raw, MAX_BYTES[lib_name], CONTENT_PREFIX[lib_name])
                probe(raw)
                file = raw
                if lib_name == "gifs":
                    file = lib / f"{item_id}.mp4"
                    gif_to_mp4(raw, file)
                    probe(file)
                    raw.unlink()
            except Exception as exc:  # noqa: BLE001
                print(f"  skip {lib_name} {item_id}: {exc}", file=sys.stderr)
                raw.unlink(missing_ok=True)
                continue
            category = QUERY_CATEGORY.get(q, "")
            tags = [
                q,
                *([category] if category else []),
                *[t["name"] for t in r.get("tags") or [] if t.get("name")][:8],
            ]
            items.append(
                _entry(
                    lib_name, item_id, file.name, r, tags=tags, width=w, height=h, category=category
                )
            )
            have.add(f"{lib_name}:{item_id}")
            seen_urls.add(r.get("foreign_landing_url"))
            got += 1
            print(f"  {lib_name}/{q}: {r.get('title')!r} ({r['license']})")
    return items


def build_sfx(f: Fetcher, root: Path, per_type: int = 3) -> list[dict[str, Any]]:
    """Short CC0 sound effects (0.2-5 s), labelled by type."""
    lib = root / "sfx"
    lib.mkdir(parents=True, exist_ok=True)
    items = _load(lib / "manifest.json")
    have = {i["id"] for i in items} | set(exclusions())
    for sfx_type, queries in SFX_TYPES.items():
        got = sum(1 for i in items if i.get("sfx_type") == sfx_type)
        for q in queries:
            if got >= per_type:
                break
            params = {"q": q, "license": "cc0", "length": "shortest,short", "page_size": 20}
            for r in _openverse(f, "audio", params):
                if got >= per_type:
                    break
                item_id = r["id"]
                dur_ms = r.get("duration") or 0
                if f"sfx:{item_id}" in have or not 200 <= dur_ms <= 5000:
                    continue
                dest = lib / f"{sfx_type}-{item_id[:8]}.mp3"
                try:
                    f.download(r["url"], dest, MAX_BYTES["music"], CONTENT_PREFIX["music"])
                    facts = probe(dest)
                except Exception as exc:  # noqa: BLE001
                    print(f"  skip sfx {item_id}: {exc}", file=sys.stderr)
                    dest.unlink(missing_ok=True)
                    continue
                items.append(
                    _entry(
                        "sfx",
                        item_id,
                        dest.name,
                        r,
                        tags=[sfx_type, *q.split()],
                        sfx_type=sfx_type,
                        duration_s=round(float(facts["format"]["duration"]), 3),
                    )
                )
                have.add(f"sfx:{item_id}")
                got += 1
                print(f"  sfx/{sfx_type}: {r.get('title')!r}")
    return items


def build_noto(f: Fetcher, root: Path) -> list[dict[str, Any]]:
    """Noto animated emoji reaction stickers (CC BY 4.0), kept as transparent GIFs."""
    lib = root / "gifs"
    lib.mkdir(parents=True, exist_ok=True)
    items = _load(lib / "manifest.json")
    have = {i["id"] for i in items} | set(exclusions())
    catalog = {i["codepoint"]: i for i in f.get_json(NOTO_CATALOG)["icons"]}
    for code, words in NOTO_REACTIONS.items():
        item_id = f"gifs:noto-{code}"
        if item_id in have or code not in catalog:
            continue
        dest = lib / f"noto-{code}.gif"
        try:
            f.download(NOTO_GIF.format(code=code), dest, MAX_BYTES["gifs"], "image/gif")
            probe(dest)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip noto {code}: {exc}", file=sys.stderr)
            dest.unlink(missing_ok=True)
            continue
        meta = catalog[code]
        tags = [*words, *(t.strip(":").replace("-", " ") for t in meta.get("tags", []))]
        title = (meta.get("tags") or [code])[0].strip(":").replace("-", " ")
        items.append(
            {
                "id": item_id,
                "file": dest.name,
                "title": f"Animated emoji: {title}",
                "tags": tags,
                "source": "noto-emoji-animation",
                "source_url": NOTO_SOURCE,
                "license": "CC-BY-4.0",
                "license_url": NOTO_LICENSE_URL,
                "attribution_required": True,
                "attribution_text": "Animated emoji: Noto Emoji Animation by Google, CC BY 4.0",
                "commercial_use": True,
                "width": 512,
                "height": 512,
                "alpha": True,
            }
        )
        print(f"  gifs/noto: {title}")
    return items


def build_memes(f: Fetcher, root: Path, count: int) -> list[dict[str, Any]]:
    lib = root / "memes"
    lib.mkdir(parents=True, exist_ok=True)
    items = _load(lib / "manifest.json")
    have = {i["id"] for i in items} | set(exclusions())
    data = f.get_json(IMGFLIP)
    for m in data["data"]["memes"]:
        if len(items) >= count:
            break
        item_id = f"memes:imgflip-{m['id']}"
        if item_id in have or is_blocked(m["name"]):
            continue
        ext = Path(urlparse(m["url"]).path).suffix or ".jpg"
        dest = lib / f"imgflip-{m['id']}{ext}"
        try:
            f.download(m["url"], dest, MAX_BYTES["memes"], CONTENT_PREFIX["memes"])
            probe(dest)
        except Exception as exc:  # noqa: BLE001
            print(f"  skip meme {m['id']}: {exc}", file=sys.stderr)
            dest.unlink(missing_ok=True)
            continue
        items.append(
            {
                "id": item_id,
                "file": dest.name,
                "title": m["name"],
                "tags": [w.lower() for w in m["name"].replace("-", " ").split() if len(w) > 2],
                "source": "imgflip",
                "source_url": f"https://imgflip.com/memetemplate/{m['id']}",
                "license": "unknown",
                "license_url": None,
                "attribution_required": False,
                "attribution_text": "",
                "commercial_use": False,  # popular templates are third-party copyrighted
                "width": m["width"],
                "height": m["height"],
                "text_boxes": m["box_count"],
            }
        )
        print(f"  memes: {m['name']!r} (personal use only)")
    return items


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--root", default="assets")
    ap.add_argument("--only", choices=["music", "images", "gifs", "memes", "sfx"], action="append")
    ap.add_argument("--pace", type=float, default=3.5, help="seconds between Openverse calls")
    ap.add_argument("--purge-only", action="store_true", help="apply exclusions, no downloads")
    args = ap.parse_args()
    root = Path(args.root)
    if args.purge_only:
        for lib in args.only or ["music", "images", "gifs", "memes", "sfx"]:
            manifest = root / lib / "manifest.json"
            kept = purge(root, lib, _load(manifest))
            manifest.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n")
            print(f"   {lib}: {len(kept)} items")
        return
    f = Fetcher(args.pace)
    libs = args.only or ["music", "images", "gifs", "memes", "sfx"]
    for lib in libs:
        print(f"== {lib}")
        if lib == "music":
            items = build_music(f, root, per_genre=5)
        elif lib == "images":
            queries = IMAGE_QUERIES + [q for q in QUERY_CATEGORY if q not in IMAGE_QUERIES]
            items = build_visual(f, root, "images", queries, per_query=2, min_side=1000)
        elif lib == "gifs":
            items = build_visual(f, root, "gifs", GIF_QUERIES, per_query=2, min_side=200)
            (root / "gifs" / "manifest.json").write_text(json.dumps(items, indent=2) + "\n")
            items = build_noto(f, root)
        elif lib == "memes":
            items = build_memes(f, root, count=100)
        elif lib == "sfx":
            items = build_sfx(f, root)
        items = purge(root, lib, items)
        (root / lib).mkdir(parents=True, exist_ok=True)
        (root / lib / "manifest.json").write_text(
            json.dumps(items, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"   {len(items)} items in {root / lib / 'manifest.json'}")


if __name__ == "__main__":
    main()
