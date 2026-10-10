"""Generated-audio library: ElevenLabs output cached by content hash (rule 5).

Files live in ``<ASSETS_DIR>/generated/`` with a manifest in the same format as the
other local libraries, so the catalog, validator and renderer treat a generated clip
like any library asset (license metadata included, rule 7). Asset ids are
``generated:<16 hex>``, derived from the request, so a repeated request is a cache hit.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from app.providers.elevenlabs import ElevenLabsClient
from app.providers.local import LibraryItem

LIBRARY = "generated"
Plan = Literal["free", "paid"]


def sfx_asset_id(prompt: str, duration_s: float) -> str:
    """Asset id of a generated sound for (prompt, duration)."""
    return _asset_id("sfx", _norm(prompt), f"{duration_s:.1f}")


def voice_asset_id(text: str, voice_id: str) -> str:
    """Asset id of a voiceover for (text, voice)."""
    return _asset_id("voice", text.strip(), voice_id)


def dub_asset_id(source_digest: str, language: str) -> str:
    """Asset id of a dubbed audio track for (source media digest, target language)."""
    return _asset_id("dub", source_digest, language)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _asset_id(*parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]
    return f"{LIBRARY}:{digest}"


def license_fields(plan: Plan) -> dict[str, object]:
    """ElevenLabs output terms by plan (ADR-017): paid plans grant a commercial licence;
    free-plan output is non-commercial and must carry "elevenlabs.io" in the post title."""
    if plan == "paid":
        return {
            "license": "ElevenLabs-paid-plan",
            "license_url": "https://elevenlabs.io/terms-of-use",
            "attribution_required": False,
            "attribution_text": "",
            "commercial_use": True,
        }
    return {
        "license": "ElevenLabs-free-plan",
        "license_url": "https://elevenlabs.io/terms-of-use",
        "attribution_required": True,
        "attribution_text": "Audio: elevenlabs.io (free plan: put elevenlabs.io in the post title)",
        "commercial_use": False,
    }


class GeneratedAudioStore:
    """Generates on a cache miss, records license metadata, returns the library item."""

    def __init__(
        self, assets_root: str | Path, client: ElevenLabsClient | None, plan: Plan, voice_id: str
    ) -> None:
        self.dir = Path(assets_root) / LIBRARY
        self.client = client
        self.plan = plan
        self.voice_id = voice_id

    @property
    def available(self) -> bool:
        """True when a key is configured (cache hits work without one)."""
        return self.client is not None

    def get(self, asset_id: str) -> LibraryItem | None:
        """Cached item, if its file exists."""
        item = next((i for i in self._read() if i.id == asset_id), None)
        return item if item and (self.dir / item.file).is_file() else None

    async def sound(self, prompt: str, duration_s: float) -> LibraryItem:
        """A sound effect for the prompt (generated once, then cached)."""
        asset_id = sfx_asset_id(prompt, duration_s)
        if (hit := self.get(asset_id)) is not None:
            return hit
        audio = await self._client().sound_effect(prompt, duration_s)
        return self.add(asset_id, audio, prompt, "sfx", duration_s)

    async def voice(self, text: str) -> LibraryItem:
        """A voiceover for the text in the configured voice."""
        asset_id = voice_asset_id(text, self.voice_id)
        if (hit := self.get(asset_id)) is not None:
            return hit
        speech = await self._client().speech(text, self.voice_id)
        return self.add(asset_id, speech.audio, text, "voiceover", speech.duration_s)

    def add(
        self, asset_id: str, audio: bytes, title: str, kind: str, duration_s: float
    ) -> LibraryItem:
        """Write the file and its manifest entry (process-safe), returning the item."""
        digest = asset_id.split(":", 1)[1]
        item = LibraryItem.model_validate(
            {
                "id": asset_id,
                "file": f"{kind}-{digest}.mp3",
                "title": title[:120],
                "tags": [kind],
                "source": "elevenlabs",
                "duration_s": round(duration_s, 3),
                "sfx_type": kind,
                **license_fields(self.plan),
            }
        )
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{item.file}.tmp"
        tmp.write_bytes(audio)
        os.replace(tmp, self.dir / item.file)
        with open(self.dir / ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            items = [i for i in self._read() if i.id != asset_id] + [item]
            out = self.dir / ".manifest.json.tmp"
            out.write_text(
                json.dumps([i.model_dump(exclude_defaults=True) for i in items], indent=2),
                encoding="utf-8",
            )
            os.replace(out, self.dir / "manifest.json")
        return item

    def _read(self) -> list[LibraryItem]:
        path = self.dir / "manifest.json"
        if not path.is_file():
            return []
        return [LibraryItem.model_validate(r) for r in json.loads(path.read_text("utf-8"))]

    def _client(self) -> ElevenLabsClient:
        if self.client is None:
            raise RuntimeError("ElevenLabs is not configured (ELEVENLABS_API_KEY)")
        return self.client
