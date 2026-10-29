"""Local synthetic backup and disposable restore drill; never restores over cgl_dev."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.common import digest, now
from app.database.config import DatabaseSettings

ROOT = Path(__file__).resolve().parents[3]
DB = [
    "docker",
    "compose",
    "--env-file",
    str(ROOT / "services/backend/.env.database"),
    "-f",
    str(ROOT / "compose.database.yaml"),
]
APP = [
    "docker",
    "compose",
    "--env-file",
    str(ROOT / "services/backend/.env.app"),
    "-f",
    str(ROOT / "compose.full.yaml"),
]


def run(command: list[str], *, data: bytes | None = None) -> bytes:
    result = subprocess.run(command, input=data, capture_output=True, check=False, timeout=180)
    if result.returncode:
        raise RuntimeError(
            "Local operator command failed; inspect service status without disclosing secrets"
        )
    return result.stdout


def backup(folder: Path) -> None:
    if folder.exists():
        raise ValueError("Choose a new backup directory; existing backups are never overwritten")
    folder.mkdir(parents=True, mode=0o700)
    try:
        run([*APP, "stop", "api", "worker", "scheduler"])
        database = run(
            [*DB, "exec", "-T", "postgres", "pg_dump", "-U", "cgl_admin", "-d", "cgl_dev", "-Fc"]
        )
        objects = run(
            [*APP, "run", "--rm", "--no-deps", "api", "tar", "-C", "/data", "-czf", "-", "."]
        )
        manifest = {
            "created_at": now().isoformat(),
            "mode": "local-synthetic-consistent-stop",
            "database_sha256": digest(database),
            "objects_sha256": digest(objects),
            "configuration": "Private .env.app and .env.database are NOT included. Back them up separately with encryption.",
        }
        for name, data in [
            ("database.dump", database),
            ("objects.tar.gz", objects),
            ("manifest.json", json.dumps(manifest, indent=2).encode()),
        ]:
            descriptor = os.open(folder / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
    finally:
        run([*APP, "start", "api", "worker", "scheduler"])
    print("Consistent synthetic database and private-file backup created.")


def drill(folder: Path) -> None:
    manifest = json.loads((folder / "manifest.json").read_text())
    database, objects = (
        (folder / "database.dump").read_bytes(),
        (folder / "objects.tar.gz").read_bytes(),
    )
    if (
        digest(database) != manifest["database_sha256"]
        or digest(objects) != manifest["objects_sha256"]
    ):
        raise ValueError("Backup integrity verification failed")
    settings = DatabaseSettings()
    assert settings.test_admin_database_url is not None
    admin_url = make_url(settings.test_admin_database_url.get_secret_value())
    if admin_url.host not in {"localhost", "127.0.0.1"} or admin_url.database != "cgl_dev":
        raise ValueError("Restore drills are restricted to local synthetic development")
    name = "cgl_restore_" + uuid4().hex
    engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with engine.connect() as connection:
        connection.exec_driver_sql(
            f'CREATE DATABASE "{name}" OWNER cgl_migrator TEMPLATE template0'
        )
    try:
        run(
            [
                *DB,
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "-U",
                "cgl_admin",
                "--no-owner",
                "--role=cgl_migrator",
                "-d",
                name,
            ],
            data=database,
        )
        restored = sa.create_engine(admin_url.set(database=name), hide_parameters=True)
        with restored.connect() as connection:
            tables = sa.inspect(connection).get_table_names(schema="app")
            if not {"tenant", "document_version", "approval", "canonical_revision"}.issubset(
                tables
            ):
                raise ValueError("Restored schema is incomplete")
            count = connection.scalar(sa.text("SELECT count(*) FROM app.tenant"))
            references = list(
                connection.execute(
                    sa.text(
                        "SELECT object_key, sha256 FROM app.document_version UNION ALL SELECT object_key, sha256 FROM app.package_artifact"
                    )
                )
            )
        restored.dispose()
        # Validate archive readability without extracting any paths or replacing current files.
        import io
        import tarfile

        with tarfile.open(fileobj=io.BytesIO(objects), mode="r:gz") as archive:
            for object_key, sha256 in references:
                referenced_file = archive.extractfile("./objects/" + object_key)
                if referenced_file is None or digest(referenced_file.read()) != sha256:
                    raise ValueError(
                        "A restored document or artifact failed integrity verification"
                    )
            for member in archive.getmembers():
                if member.isfile():
                    source = archive.extractfile(member)
                    if source:
                        source.read()
        print(
            f"Disposable restore drill passed: {len(tables)} tables, {count} fictional agencies, {len(references)} referenced document/artifact hashes verified."
        )
    finally:
        with engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["backup", "drill"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    backup(args.directory) if args.operation == "backup" else drill(args.directory)


if __name__ == "__main__":
    main()
