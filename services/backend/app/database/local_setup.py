"""Generate local-only credentials without overwriting an existing database configuration.

This module uses only the standard library so Docker users need no installed Python packages.
"""

import argparse
import os
import secrets
from pathlib import Path


def create_configuration(destination: Path, port: int = 55432) -> bool:
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    admin_password = secrets.token_hex(32)
    migration_password = secrets.token_hex(32)
    contents = (
        "# Generated local-only credentials. Keep private; preserve with the database volume.\n"
        "CGL_APP_ENV=local\n"
        f"CGL_POSTGRES_PORT={port}\n"
        f"CGL_POSTGRES_ADMIN_PASSWORD={admin_password}\n"
        f"CGL_POSTGRES_MIGRATION_PASSWORD={migration_password}\n"
        "CGL_MIGRATION_DATABASE_URL="
        f"postgresql+psycopg://cgl_migrator:{migration_password}@127.0.0.1:{port}/cgl_dev\n"
        "CGL_TEST_ADMIN_DATABASE_URL="
        f"postgresql+psycopg://cgl_admin:{admin_password}@127.0.0.1:{port}/cgl_dev\n"
    )
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(contents)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Create private local PostgreSQL configuration")
    parser.add_argument("--port", type=int, default=55432)
    arguments = parser.parse_args()
    destination = Path(__file__).resolve().parents[2] / ".env.database"
    try:
        created = create_configuration(destination, arguments.port)
    except ValueError as error:
        parser.error(str(error))
    print(
        "Created services/backend/.env.database (private local credentials)."
        if created
        else "Existing services/backend/.env.database preserved; credentials were not changed."
    )


if __name__ == "__main__":
    main()
