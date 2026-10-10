"""ElevenLabs adapter (httpx): sound effects, speech with timestamps, dubbing.

Endpoints (read 2026-10-10, recorded in ADR-017):
- POST /v1/sound-generation                      https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert
- POST /v1/text-to-speech/{voice_id}/with-timestamps  https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps
- POST /v1/dubbing, GET /v1/dubbing/{id}, GET /v1/dubbing/{id}/audio/{lang}
                                                 https://elevenlabs.io/docs/api-reference/dubbing/create

Responses are UNTRUSTED DATA (rule 6): only audio bytes, timings and status strings are
read, never followed as instructions. Business logic uses ``GeneratedAudioStore``.
"""

import base64
from dataclasses import dataclass

import httpx

BASE_URL = "https://api.elevenlabs.io"
SFX_MODEL = "eleven_text_to_sound_v2"
TTS_MODEL = "eleven_multilingual_v2"  # en + hi
OUTPUT_FORMAT = "mp3_44100_128"


class ElevenLabsError(Exception):
    """Request failed. ``code`` is auth | quota | bad_request | unavailable | failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Speech:
    """Synthesised speech plus its length from the character alignment."""

    audio: bytes
    duration_s: float


@dataclass(frozen=True)
class DubJob:
    """A started dubbing project."""

    dubbing_id: str
    expected_s: float


class ElevenLabsClient:
    """Thin async client. ``transport`` lets tests inject ``httpx.MockTransport``."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        if not api_key:
            raise ValueError("ElevenLabs API key is empty")
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"xi-api-key": api_key},
            transport=transport,
            timeout=timeout_s,
        )

    async def aclose(self) -> None:
        """Close the HTTP connection pool."""
        await self._client.aclose()

    async def sound_effect(
        self, text: str, duration_s: float, prompt_influence: float = 0.4
    ) -> bytes:
        """MP3 bytes for a described sound (``duration_s`` 0.5..30)."""
        if not 0.5 <= duration_s <= 30.0:
            raise ValueError("duration_s must be within 0.5..30")
        resp = await self._post(
            "/v1/sound-generation",
            params={"output_format": OUTPUT_FORMAT},
            json={
                "text": text,
                "duration_seconds": duration_s,
                "prompt_influence": prompt_influence,
                "model_id": SFX_MODEL,
            },
        )
        return resp.content

    async def speech(self, text: str, voice_id: str) -> Speech:
        """Speech MP3 and its duration (last character end time). Multilingual v2 detects
        the language from the text, so en and hi need no language code."""
        body: dict[str, object] = {"text": text, "model_id": TTS_MODEL}
        resp = await self._post(
            f"/v1/text-to-speech/{_safe_id(voice_id)}/with-timestamps",
            params={"output_format": OUTPUT_FORMAT},
            json=body,
        )
        data = resp.json()
        try:
            audio = base64.b64decode(data["audio_base64"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ElevenLabsError("failed", "speech response had no audio") from exc
        ends = (data.get("alignment") or {}).get("character_end_times_seconds") or [0.0]
        return Speech(audio=audio, duration_s=float(max(ends)))

    async def start_dub(
        self, media: bytes, filename: str, source_lang: str, target_lang: str
    ) -> DubJob:
        """Upload audio/video for dubbing; returns the project id to poll."""
        resp = await self._post(
            "/v1/dubbing",
            files={"file": (filename, media)},
            data={
                "source_lang": source_lang,
                "target_lang": target_lang,
                "num_speakers": "0",  # auto-detect
                "watermark": "false",
            },
        )
        data = resp.json()
        dub_id = data.get("dubbing_id")
        if not isinstance(dub_id, str) or not dub_id:
            raise ElevenLabsError("failed", "dubbing response had no dubbing_id")
        return DubJob(dub_id, float(data.get("expected_duration_sec") or 0.0))

    async def dub_status(self, dubbing_id: str) -> str:
        """'dubbed' when ready, 'failed' on error, else the in-progress status."""
        resp = await self._request("GET", f"/v1/dubbing/{_safe_id(dubbing_id)}")
        data = resp.json()
        if data.get("error"):
            return "failed"
        return str(data.get("status") or "dubbing")

    async def dubbed_audio(self, dubbing_id: str, language: str) -> bytes:
        """The automatic dub as MP3/MP4 bytes (ffmpeg reads either)."""
        resp = await self._request(
            "GET", f"/v1/dubbing/{_safe_id(dubbing_id)}/audio/{_safe_id(language)}"
        )
        return resp.content

    async def _post(self, path: str, **kwargs: object) -> httpx.Response:
        return await self._request("POST", path, **kwargs)

    async def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            resp = await self._client.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as exc:
            raise ElevenLabsError("unavailable", type(exc).__name__) from exc
        if resp.status_code in (401, 403):
            raise ElevenLabsError("auth", "API key rejected or missing permission")
        if resp.status_code == 429:
            raise ElevenLabsError("quota", "rate limit or character quota exceeded")
        if resp.status_code in (400, 422):
            raise ElevenLabsError("bad_request", _detail(resp))
        if resp.status_code >= 400:
            raise ElevenLabsError("unavailable", f"HTTP {resp.status_code}")
        return resp


def _safe_id(value: str) -> str:
    """Path segments are ids we got from config or ElevenLabs: allow only [A-Za-z0-9_-]."""
    if not value or not all(c.isalnum() or c in "-_" for c in value):
        raise ValueError(f"unsafe id {value!r}")
    return value


def _detail(resp: httpx.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except ValueError:
        return f"HTTP {resp.status_code}"
    if isinstance(detail, dict):
        detail = detail.get("message")
    return str(detail)[:200] if detail else f"HTTP {resp.status_code}"
