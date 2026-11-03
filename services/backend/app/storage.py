"""Private object access. Object keys are server-generated and never accepted from clients."""

import fcntl
import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings


class Store(Protocol):
    def put(
        self, key: str, body: bytes, content_type: str, *, not_after: datetime | None = None
    ) -> str | None: ...
    def get(self, key: str, version: str | None = None) -> bytes: ...
    def delete(self, key: str, version: str | None = None) -> None: ...
    def probe(self, key: str) -> tuple[int, str] | None: ...


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


@dataclass
class TenantScan:
    """Retained logical bytes/entries under one tenant directory, counted as ADR 0006 does."""

    byte_size: int = 0
    entries: int = 0
    files: dict[str, int] = field(default_factory=dict)  # Relative key -> byte size.
    anomalies: int = 0  # Symlinks or special files: never followed, never charged as bytes.


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def write_lock(self) -> Iterator[None]:
        # All local writers share the private volume. The kernel releases this
        # lock on crash. Admission is the PostgreSQL reservation ledger (ADR 0008);
        # this lock serializes exclusive creates, cleanup and reconciliation scans.
        fd = os.open(
            self.root / ".storage-write.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def scan_tenant(self, tenant: UUID) -> TenantScan:
        """Walk only ``tenants/{tenant}/``. Callers hold ``write_lock`` for a consistent count."""
        result = TenantScan()
        top = self.root / "tenants" / str(tenant)
        try:
            mode = top.lstat().st_mode
        except FileNotFoundError:
            return result
        if not stat.S_ISDIR(mode):
            result.anomalies += 1
            return result
        result.entries += 1
        pending = [top]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as children:
                for entry in children:
                    result.entries += 1
                    if entry.is_symlink():
                        result.anomalies += 1
                    elif entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        size = entry.stat(follow_symlinks=False).st_size
                        result.byte_size += size
                        result.files[Path(entry.path).relative_to(self.root).as_posix()] = size
                    else:
                        result.anomalies += 1
        return result

    def path(self, key: str) -> Path:
        validate_key(key)
        result = (self.root / key).resolve()
        if not result.is_relative_to(self.root):
            raise ValueError("Invalid object path")
        return result

    def probe(self, key: str) -> tuple[int, str] | None:
        """Size and SHA-256 of a complete regular object, or None when absent."""
        path = self.path(key)
        with self.write_lock():
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                return None
            with os.fdopen(fd, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError("Private object is not a regular file")
                checksum, size = hashlib.sha256(), 0
                while chunk := source.read(1024 * 1024):
                    checksum.update(chunk)
                    size += len(chunk)
            return size, checksum.hexdigest()

    def put(
        self, key: str, body: bytes, content_type: str, *, not_after: datetime | None = None
    ) -> str | None:
        path = self.path(key)
        with self.write_lock():
            # Checked under the lock: a writer whose reservation is about to lapse
            # must not race the orphan planner, which inventories under this lock.
            if not_after is not None and datetime.now(UTC) >= not_after:
                raise StorageCapacityError("STORAGE_QUOTA_UNAVAILABLE")
            if path.exists():
                raise FileExistsError("Private object already exists")
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

    def put(
        self, key: str, body: bytes, content_type: str, *, not_after: datetime | None = None
    ) -> str | None:
        validate_key(key)
        # Version-addressed reads remain available. The PostgreSQL reservation
        # ledger is adapter-neutral (ADR 0008), but conditional immutable writes
        # and uncertain-write reconciliation need a local S3-compatible service
        # and release evidence before this gate can open.
        raise StorageCapacityError("STORAGE_QUOTA_UNAVAILABLE")

    def probe(self, key: str) -> tuple[int, str] | None:
        validate_key(key)
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
        else FileStore(configuration.storage_path)
    )


if __name__ == "__main__":
    S3Store(Settings()).initialize_local()
    print("Private local object bucket initialized with versioning.")
