"""Private object access. Object keys are server-generated and never accepted from clients."""

import fcntl
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings


class Store(Protocol):
    def put(self, key: str, body: bytes, content_type: str) -> str | None: ...
    def get(self, key: str, version: str | None = None) -> bytes: ...
    def delete(self, key: str, version: str | None = None) -> None: ...


class StorageCapacityError(Exception):
    """A storage budget would be exceeded or cannot yet be enforced."""

    def __init__(
        self,
        code: Literal[
            "STORAGE_CAPACITY_REACHED", "STORAGE_QUOTA_UNAVAILABLE"
        ] = "STORAGE_CAPACITY_REACHED",
    ) -> None:
        self.code = code
        super().__init__(self.code)


def validate_key(key: str) -> None:
    if not re.fullmatch(r"tenants/[0-9a-f-]{36}/[a-z]+/[0-9a-f-]{36}/[a-zA-Z0-9._-]+", key):
        raise ValueError("Invalid private object key")


class FileStore:
    def __init__(
        self,
        root: Path,
        *,
        tenant_byte_limit: int = 2 * 1024**3,
        total_byte_limit: int = 20 * 1024**3,
        entry_limit: int = 100_000,
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.tenant_byte_limit = tenant_byte_limit
        self.total_byte_limit = total_byte_limit
        self.entry_limit = entry_limit

    @contextmanager
    def write_lock(self) -> Iterator[None]:
        # All local writers share the private volume. The kernel releases this
        # lock on crash; quota is reconstructed from retained bytes, never from
        # a business transaction that could roll back after an object write.
        fd = os.open(
            self.root / ".storage-write.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def require_capacity(self, key: str, byte_size: int) -> None:
        tenant_root = self.root / "tenants" / key.split("/")[1]
        total_bytes, tenant_bytes, entries = byte_size, byte_size, 1
        parent = self.path(key).parent
        while parent != self.root and not parent.exists():
            entries += 1
            parent = parent.parent
        pending = [self.root]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as children:
                for entry in children:
                    if directory == self.root and entry.name == ".storage-write.lock":
                        continue
                    entries += 1
                    if entries > self.entry_limit or entry.is_symlink():
                        raise StorageCapacityError()
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        size = entry.stat(follow_symlinks=False).st_size
                        total_bytes += size
                        if Path(entry.path).is_relative_to(tenant_root):
                            tenant_bytes += size
                    else:
                        raise StorageCapacityError()
                    if total_bytes > self.total_byte_limit or tenant_bytes > self.tenant_byte_limit:
                        raise StorageCapacityError()
        if (
            total_bytes > self.total_byte_limit
            or tenant_bytes > self.tenant_byte_limit
            or entries > self.entry_limit
        ):
            raise StorageCapacityError()

    def path(self, key: str) -> Path:
        validate_key(key)
        result = (self.root / key).resolve()
        if not result.is_relative_to(self.root):
            raise ValueError("Invalid object path")
        return result

    def put(self, key: str, body: bytes, content_type: str) -> str | None:
        path = self.path(key)
        with self.write_lock():
            if path.exists():
                raise FileExistsError("Private object already exists")
            self.require_capacity(key, len(body))
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(body)
        return None

    def get(self, key: str, version: str | None = None) -> bytes:
        return self.path(key).read_bytes()

    def delete(self, key: str, version: str | None = None) -> None:
        path = self.path(key)
        with self.write_lock():
            path.unlink(missing_ok=True)


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
        # Version-addressed reads remain available. A distributed reservation and
        # uncertain-write reconciliation protocol is required before S3 intake
        # can enforce the same retained-byte limits as the local shared volume.
        raise StorageCapacityError("STORAGE_QUOTA_UNAVAILABLE")

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
        else FileStore(
            configuration.storage_path,
            tenant_byte_limit=configuration.storage_tenant_byte_limit,
            total_byte_limit=configuration.storage_total_byte_limit,
            entry_limit=configuration.storage_entry_limit,
        )
    )


if __name__ == "__main__":
    S3Store(Settings()).initialize_local()
    print("Private local object bucket initialized with versioning.")
