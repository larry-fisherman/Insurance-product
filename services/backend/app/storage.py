"""Private object access. Object keys are server-generated and never accepted from clients."""

import os
import re
from pathlib import Path
from typing import Any, Protocol, cast

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings


class Store(Protocol):
    def put(self, key: str, body: bytes, content_type: str) -> str | None: ...
    def get(self, key: str, version: str | None = None) -> bytes: ...
    def delete(self, key: str, version: str | None = None) -> None: ...


def validate_key(key: str) -> None:
    if not re.fullmatch(r"tenants/[0-9a-f-]{36}/[a-z]+/[0-9a-f-]{36}/[a-zA-Z0-9._-]+", key):
        raise ValueError("Invalid private object key")


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, key: str) -> Path:
        validate_key(key)
        result = (self.root / key).resolve()
        if not result.is_relative_to(self.root):
            raise ValueError("Invalid object path")
        return result

    def put(self, key: str, body: bytes, content_type: str) -> str | None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(body)
        return None

    def get(self, key: str, version: str | None = None) -> bytes:
        return self.path(key).read_bytes()

    def delete(self, key: str, version: str | None = None) -> None:
        self.path(key).unlink(missing_ok=True)


class S3Store:
    def __init__(self, configuration: Settings) -> None:
        self.bucket = configuration.s3_bucket
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=configuration.s3_endpoint,
            aws_access_key_id=configuration.s3_access_key.get_secret_value()
            if configuration.s3_access_key
            else None,
            aws_secret_access_key=configuration.s3_secret_key.get_secret_value()
            if configuration.s3_secret_key
            else None,
            region_name="us-east-1",
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 2},
                s3={"addressing_style": "path"},
            ),
        )

    def initialize_local(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as error:
            if str(error.response["Error"]["Code"]) not in {"404", "NoSuchBucket"}:
                raise
            self.client.create_bucket(Bucket=self.bucket)
        self.client.put_bucket_versioning(
            Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"}
        )

    def put(self, key: str, body: bytes, content_type: str) -> str | None:
        validate_key(key)
        result = self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            IfNoneMatch="*",
            Metadata={"privacy": "tenant-private"},
        )
        return cast(str | None, result.get("VersionId"))

    def get(self, key: str, version: str | None = None) -> bytes:
        validate_key(key)
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if version:
            kwargs["VersionId"] = version
        response = self.client.get_object(**kwargs)
        try:
            return cast(bytes, response["Body"].read())
        finally:
            response["Body"].close()

    def delete(self, key: str, version: str | None = None) -> None:
        validate_key(key)
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if version:
            kwargs["VersionId"] = version
        self.client.delete_object(**kwargs)


def storage(configuration: Settings) -> Store:
    return (
        S3Store(configuration)
        if configuration.storage_backend == "s3"
        else FileStore(configuration.storage_path)
    )


if __name__ == "__main__":
    S3Store(Settings()).initialize_local()
    print("Private local object bucket initialized with versioning.")
