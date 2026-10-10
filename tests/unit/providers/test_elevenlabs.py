"""ElevenLabs adapter against a mock transport: request shapes, parsing, error mapping."""

import base64
import json

import httpx
import pytest

from app.providers.elevenlabs import ElevenLabsClient, ElevenLabsError


def _client(handler) -> ElevenLabsClient:  # type: ignore[no-untyped-def]
    return ElevenLabsClient("k-test", transport=httpx.MockTransport(handler))


async def test_sound_effect_request_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=b"MP3")

    client = _client(handler)
    assert await client.sound_effect("cash register", 1.5) == b"MP3"
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/v1/sound-generation"
    assert req.headers["xi-api-key"] == "k-test"
    assert req.url.params["output_format"] == "mp3_44100_128"
    body = json.loads(req.content)
    assert body["text"] == "cash register" and body["duration_seconds"] == 1.5
    assert body["model_id"] == "eleven_text_to_sound_v2"
    with pytest.raises(ValueError):
        await client.sound_effect("x", 31.0)


async def test_speech_decodes_audio_and_duration() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/text-to-speech/voice_1/with-timestamps"
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"VOICE").decode(),
                "alignment": {"character_end_times_seconds": [0.1, 0.9, 1.75]},
            },
        )

    speech = await _client(handler).speech("hello", "voice_1")
    assert speech.audio == b"VOICE" and speech.duration_s == 1.75


async def test_dubbing_round_trip() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            assert req.url.path == "/v1/dubbing"
            assert b'name="target_lang"' in req.content and b"hi" in req.content
            return httpx.Response(200, json={"dubbing_id": "d1", "expected_duration_sec": 30})
        if req.url.path == "/v1/dubbing/d1":
            return httpx.Response(200, json={"dubbing_id": "d1", "status": "dubbed"})
        assert req.url.path == "/v1/dubbing/d1/audio/hi"
        return httpx.Response(200, content=b"DUB")

    client = _client(handler)
    job = await client.start_dub(b"audio", "speech.mp3", "en", "hi")
    assert job.dubbing_id == "d1" and job.expected_s == 30
    assert await client.dub_status("d1") == "dubbed"
    assert await client.dubbed_audio("d1", "hi") == b"DUB"


async def test_dub_error_field_means_failed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "dubbing", "error": "bad media"})

    assert await _client(handler).dub_status("d1") == "failed"


@pytest.mark.parametrize(
    ("status", "code"), [(401, "auth"), (429, "quota"), (422, "bad_request"), (503, "unavailable")]
)
async def test_http_errors_map_to_codes(status: int, code: str) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": {"message": "nope"}})

    with pytest.raises(ElevenLabsError) as exc:
        await _client(handler).sound_effect("boom", 1.0)
    assert exc.value.code == code


async def test_path_ids_are_validated() -> None:
    client = _client(lambda req: httpx.Response(200))
    with pytest.raises(ValueError):
        await client.dub_status("../voices")
    with pytest.raises(ValueError):
        await client.speech("hi", "a/b")


def test_empty_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        ElevenLabsClient("")
