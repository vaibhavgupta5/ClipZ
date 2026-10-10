"""Storage unit tests: MemoryStorage behavior + S3Storage contract (no network)."""

import pytest

from app.storage import MemoryStorage, S3Storage, Storage, validate_key


def test_memory_storage_round_trip() -> None:
    store: Storage = MemoryStorage()
    store.put_bytes("proj/a.bin", b"hello", "application/octet-stream")
    assert store.get_bytes("proj/a.bin") == b"hello"
    store.delete("proj/a.bin")
    with pytest.raises(KeyError):
        store.get_bytes("proj/a.bin")


def test_validate_key_rejects_traversal() -> None:
    for bad in ["", "../etc/passwd", "a/../../b", "/absolute", "x" * 1025]:
        with pytest.raises(ValueError):
            validate_key(bad)
    validate_key("proj-1/normalized/master.mp4")  # must not raise


def test_s3_presigned_url_uses_public_endpoint() -> None:
    store = S3Storage(
        endpoint="http://internal:3900",
        bucket="reelforge",
        access_key="a",
        secret_key="b",
        public_endpoint="http://public:9000",
    )
    url = store.presigned_upload_url("proj/x.mp4", "video/mp4", expires_s=60)
    assert url.startswith("http://public:9000/reelforge/proj/x.mp4")


def test_s3_presigned_url_shape_without_network() -> None:
    store = S3Storage(
        endpoint="http://localhost:9000", bucket="reelforge", access_key="a", secret_key="b"
    )
    url = store.presigned_upload_url("proj/x.mp4", "video/mp4", expires_s=60)
    assert url.startswith("http://localhost:9000/reelforge/proj/x.mp4")
    assert "X-Amz-Expires=60" in url


def test_s3_storage_put_get_with_stubber() -> None:
    import io

    import boto3
    from botocore.response import StreamingBody
    from botocore.stub import Stubber

    store = S3Storage(
        endpoint="http://localhost:9000", bucket="reelforge", access_key="a", secret_key="b"
    )
    client = boto3.client(
        "s3",
        endpoint_url="http://localhost:9000",
        aws_access_key_id="a",
        aws_secret_access_key="b",
        region_name="us-east-1",
    )
    stubber = Stubber(client)
    stubber.add_response(
        "put_object",
        {},
        {
            "Bucket": "reelforge",
            "Key": "k",
            "Body": b"data",
            "ContentType": "application/octet-stream",
        },
    )
    stubber.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(b"data"), 4)},
        {"Bucket": "reelforge", "Key": "k"},
    )
    stubber.activate()
    store._client = client  # noqa: SLF001 - test seam
    store.put_bytes("k", b"data", "application/octet-stream")
    assert store.get_bytes("k") == b"data"
    stubber.deactivate()
