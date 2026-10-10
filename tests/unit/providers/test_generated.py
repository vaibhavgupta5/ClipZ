"""Generated-audio store: cached by content hash, license by plan, visible to the catalog."""

from pathlib import Path

import httpx

from app.providers.elevenlabs import ElevenLabsClient
from app.providers.generated import GeneratedAudioStore, sfx_asset_id, voice_asset_id
from app.providers.local import AssetCatalog, resolve_asset_file


def _store(tmp_path: Path, plan: str = "free") -> tuple[GeneratedAudioStore, list[str]]:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if "text-to-speech" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "audio_base64": "Vk9JQ0U=",
                    "alignment": {"character_end_times_seconds": [1.2]},
                },
            )
        return httpx.Response(200, content=b"SFX")

    client = ElevenLabsClient("k", transport=httpx.MockTransport(handler))
    return GeneratedAudioStore(tmp_path, client, plan, "v1"), calls  # type: ignore[arg-type]


async def test_sound_is_generated_once_then_cached(tmp_path: Path) -> None:
    store, calls = _store(tmp_path)
    first = await store.sound("Cash  Register", 1.0)
    again = await store.sound("cash register", 1.0)  # same after normalisation
    assert first.id == again.id == sfx_asset_id("cash register", 1.0)
    assert calls == ["/v1/sound-generation"]
    assert (tmp_path / "generated" / first.file).read_bytes() == b"SFX"


async def test_free_plan_is_personal_use_and_credited(tmp_path: Path) -> None:
    store, _ = _store(tmp_path, "free")
    item = await store.voice("hello there")
    assert item.id == voice_asset_id("hello there", "v1")
    assert not item.commercial_use and item.attribution_required
    assert item.duration_s == 1.2 and item.license


async def test_paid_plan_is_commercial(tmp_path: Path) -> None:
    store, _ = _store(tmp_path, "paid")
    item = await store.sound("crowd gasp", 2.0)
    assert item.commercial_use and not item.attribution_required


async def test_catalog_and_renderer_resolve_generated_assets(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    catalog = AssetCatalog(tmp_path, tmp_path / "memes")
    assert catalog.get(sfx_asset_id("door slam", 1.0)) is None
    item = await store.sound("door slam", 1.0)
    catalog.reload("generated")
    found = catalog.get(item.id)
    assert found is not None and found[0] == "generated"
    path = resolve_asset_file(tmp_path, tmp_path / "memes", item.id)
    assert path is not None and path.read_bytes() == b"SFX"


def test_unavailable_without_client(tmp_path: Path) -> None:
    assert not GeneratedAudioStore(tmp_path, None, "free", "v1").available
