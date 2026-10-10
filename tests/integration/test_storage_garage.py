"""Storage integration test against local Garage (S3-compatible). Opt-in: pytest -m integration."""

import uuid

import httpx
import pytest

from app.settings import get_settings
from app.storage import get_storage

pytestmark = pytest.mark.integration


def _key() -> str:
    return f"itest/{uuid.uuid4().hex}/roundtrip.bin"


def test_presigned_upload_download_round_trip() -> None:
    settings = get_settings()
    store = get_storage(settings)
    store.ensure_bucket()

    key = _key()
    payload = b"reelforge-integration-probe-" + uuid.uuid4().hex.encode()

    put_url = store.presigned_upload_url(key, "application/octet-stream", expires_s=300)
    put_resp = httpx.put(
        put_url, content=payload, headers={"Content-Type": "application/octet-stream"}
    )
    assert put_resp.status_code in (200, 201), put_resp.text

    assert store.get_bytes(key) == payload

    get_url = store.presigned_download_url(key, expires_s=300)
    get_resp = httpx.get(get_url)
    assert get_resp.status_code == 200
    assert get_resp.content == payload

    store.delete(key)
    with pytest.raises(KeyError):
        store.get_bytes(key)
