from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO


@dataclass(frozen=True)
class StoredObject:
    body: bytes
    content_type: str


class StorageClient:
    def __init__(
        self,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        bucket: str | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url or os.environ.get("S3_ENDPOINT_URL")
        self.access_key_id = access_key_id or os.environ.get("S3_ACCESS_KEY_ID")
        self.secret_access_key = secret_access_key or os.environ.get("S3_SECRET_ACCESS_KEY")
        self.bucket = bucket or os.environ.get("S3_BUCKET", "vvault")
        if not self.endpoint_url:
            raise RuntimeError("S3_ENDPOINT_URL is required")
        if not self.access_key_id:
            raise RuntimeError("S3_ACCESS_KEY_ID is required")
        if not self.secret_access_key:
            raise RuntimeError("S3_SECRET_ACCESS_KEY is required")

    def _client(self) -> object:
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=os.environ.get("S3_REGION", "us-east-1"),
        )

    def ensure_bucket(self) -> None:
        client = self._client()
        try:
            client.head_bucket(Bucket=self.bucket)
        except Exception:
            client.create_bucket(Bucket=self.bucket)

    def healthcheck(self) -> bool:
        self._client().list_buckets()
        return True

    def upload_bytes(
        self,
        *,
        object_key: str,
        body: bytes,
        content_type: str,
        bucket: str | None = None,
    ) -> None:
        target_bucket = bucket or self.bucket
        self.ensure_bucket()
        self._client().upload_fileobj(
            BytesIO(body),
            target_bucket,
            object_key,
            ExtraArgs={"ContentType": content_type},
        )

    def download_bytes(self, *, bucket: str, object_key: str) -> StoredObject:
        response = self._client().get_object(Bucket=bucket, Key=object_key)
        body = response["Body"].read()
        return StoredObject(body=body, content_type=response.get("ContentType", "application/octet-stream"))

    def object_exists(self, *, bucket: str, object_key: str) -> bool:
        try:
            self._client().head_object(Bucket=bucket, Key=object_key)
            return True
        except Exception:
            return False
