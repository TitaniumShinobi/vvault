from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote


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
        object_storage_url: str | None = None,
        object_storage_service_key: str | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url or os.environ.get("S3_ENDPOINT_URL")
        self.access_key_id = access_key_id or os.environ.get("S3_ACCESS_KEY_ID")
        self.secret_access_key = secret_access_key or os.environ.get("S3_SECRET_ACCESS_KEY")
        self.object_storage_url = (
            object_storage_url
            or os.environ.get("VVAULT_OBJECT_STORAGE_URL")
            or os.environ.get("VVAULT_BODY_DB_URL")
            or os.environ.get("SUPABASE_URL")
        )
        self.object_storage_service_key = (
            object_storage_service_key
            or os.environ.get("VVAULT_OBJECT_STORAGE_SERVICE_KEY")
            or os.environ.get("VVAULT_BODY_DB_SERVICE_ROLE_KEY")
            or os.environ.get("VVAULT_BODY_DB_ANON_KEY")
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("SUPABASE_ANON_KEY")
        )
        self.bucket = (
            bucket
            or os.environ.get("S3_BUCKET")
            or os.environ.get("VVAULT_STORAGE_BUCKET")
            or os.environ.get("SUPABASE_STORAGE_BUCKET")
            or "vvault"
        )
        if self.endpoint_url and self.access_key_id and self.secret_access_key:
            self.provider = "s3_compatible"
        elif self.object_storage_url and self.object_storage_service_key:
            self.provider = "object_storage_rest"
            self.object_storage_url = self.object_storage_url.rstrip("/")
            for suffix in ("/rest/v1", "/storage/v1"):
                if self.object_storage_url.endswith(suffix):
                    self.object_storage_url = self.object_storage_url[: -len(suffix)]
                    break
        else:
            raise RuntimeError(
                "S3 credentials or VVAULT_OBJECT_STORAGE_URL and "
                "VVAULT_OBJECT_STORAGE_SERVICE_KEY are required"
            )

    def _client(self) -> object:
        if self.provider != "s3_compatible":
            raise RuntimeError("S3 client is unavailable for the configured storage provider")
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=os.environ.get("S3_REGION", "us-east-1"),
        )

    def _rest_headers(self) -> dict[str, str]:
        return {
            "apikey": str(self.object_storage_service_key),
            "Authorization": f"Bearer {self.object_storage_service_key}",
        }

    def _rest_object_url(self, bucket: str, object_key: str) -> str:
        encoded_bucket = quote(str(bucket).strip(), safe="")
        encoded_key = "/".join(
            quote(part, safe="")
            for part in str(object_key).strip().lstrip("/").split("/")
            if part
        )
        return f"{self.object_storage_url}/storage/v1/object/{encoded_bucket}/{encoded_key}"

    def _rest_request(self, method: str, url: str, **kwargs):
        import requests

        return requests.request(method, url, **kwargs)

    def ensure_bucket(self) -> None:
        if self.provider == "object_storage_rest":
            response = self._rest_request(
                "GET",
                f"{self.object_storage_url}/storage/v1/bucket/{quote(self.bucket, safe='')}",
                headers=self._rest_headers(),
                timeout=20,
            )
            response.raise_for_status()
            return
        client = self._client()
        try:
            client.head_bucket(Bucket=self.bucket)
        except Exception:
            client.create_bucket(Bucket=self.bucket)

    def healthcheck(self) -> bool:
        if self.provider == "object_storage_rest":
            response = self._rest_request(
                "GET",
                f"{self.object_storage_url}/storage/v1/bucket",
                headers=self._rest_headers(),
                timeout=20,
            )
            response.raise_for_status()
            return True
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
        if self.provider != "s3_compatible":
            raise RuntimeError("Object-storage REST bridge is read-only")
        target_bucket = bucket or self.bucket
        self.ensure_bucket()
        self._client().upload_fileobj(
            BytesIO(body),
            target_bucket,
            object_key,
            ExtraArgs={"ContentType": content_type},
        )

    def download_bytes(self, *, bucket: str, object_key: str) -> StoredObject:
        if self.provider == "object_storage_rest":
            response = self._rest_request(
                "GET",
                self._rest_object_url(bucket, object_key),
                headers=self._rest_headers(),
                timeout=30,
            )
            response.raise_for_status()
            return StoredObject(
                body=response.content,
                content_type=response.headers.get("Content-Type", "application/octet-stream"),
            )
        response = self._client().get_object(Bucket=bucket, Key=object_key)
        body = response["Body"].read()
        return StoredObject(body=body, content_type=response.get("ContentType", "application/octet-stream"))

    def object_exists(self, *, bucket: str, object_key: str) -> bool:
        if self.provider == "object_storage_rest":
            try:
                response = self._rest_request(
                    "HEAD",
                    self._rest_object_url(bucket, object_key),
                    headers=self._rest_headers(),
                    timeout=20,
                )
                return response.status_code == 200
            except Exception:
                return False
        try:
            self._client().head_object(Bucket=bucket, Key=object_key)
            return True
        except Exception:
            return False
