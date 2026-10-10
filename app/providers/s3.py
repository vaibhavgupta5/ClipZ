"""S3-compatible object storage provider (DigitalOcean Spaces, AWS S3, Cloudflare R2, MinIO).

Vendor SDK (boto3) is strictly encapsulated within app/providers/ (Rule 8).
"""

from typing import Any


class S3Client:
    """Encapsulated S3 client for object storage operations."""

    def __init__(
        self,
        bucket_name: str,
        endpoint_url: str | None = None,
        region_name: str = "us-east-1",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket_name
        cfg = Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"})
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region_name,
            aws_access_key_id=aws_access_key_id or None,
            aws_secret_access_key=aws_secret_access_key or None,
            config=cfg,
        )

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def get_object(self, key: str) -> tuple[bytes, str]:
        resp = self._client.get_object(Bucket=self.bucket, Key=key)
        data: bytes = resp["Body"].read()
        content_type: str = resp.get("ContentType", "application/octet-stream")
        return data, content_type

    def download_file(self, key: str, target_path: str) -> None:
        self._client.download_file(self.bucket, key, target_path)

    def head_object(self, key: str) -> dict[str, Any]:
        return dict(self._client.head_object(Bucket=self.bucket, Key=key))

    def delete_object(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})

    def head_bucket(self) -> bool:
        self._client.head_bucket(Bucket=self.bucket)
        return True
