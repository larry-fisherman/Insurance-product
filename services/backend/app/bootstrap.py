"""Local configuration/role provisioning. Never used by request handlers or production startup."""

import os
import secrets
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.database.config import DatabaseSettings

ROOT = Path(__file__).resolve().parents[1]


def configure() -> None:
    target = ROOT / ".env.app"
    if target.exists():
        print("Existing private application configuration preserved.")
        return
    runtime, auth, redis, storage, secret = (secrets.token_hex(32) for _ in range(5))
    db = DatabaseSettings()
    base = make_url(db.migration_database_url.get_secret_value())
    runtime_url = base.set(username="cgl_runtime", password=runtime).render_as_string(
        hide_password=False
    )
    auth_url = base.set(username="cgl_auth", password=auth).render_as_string(hide_password=False)
    values = {
        "CGL_APP_ENV": "local",
        "CGL_SYNTHETIC_ONLY": "true",
        "CGL_APP_SECRET": secret,
        "CGL_RUNTIME_PASSWORD": runtime,
        "CGL_AUTH_PASSWORD": auth,
        "CGL_RUNTIME_DATABASE_URL": runtime_url,
        "CGL_AUTH_DATABASE_URL": auth_url,
        "CGL_REDIS_PASSWORD": redis,
        "CGL_REDIS_URL": f"redis://:{redis}@127.0.0.1:56379/0",
        "CGL_STORAGE_BACKEND": "filesystem",
        "CGL_STORAGE_PATH": str(ROOT / ".data" / "objects"),
        "CGL_S3_ENDPOINT": "http://127.0.0.1:59000",
        "CGL_S3_BUCKET": "cgl-private",
        "CGL_S3_ACCESS_KEY": "cgl-local-store",
        "CGL_S3_SECRET_KEY": storage,
        "CGL_PUBLIC_ORIGIN": "http://localhost:3000",
        "CGL_CLAMAV_HOST": "127.0.0.1",
        "CGL_CLAMAV_PORT": "53310",
    }
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write("# Local synthetic review environment. Never commit this file.\n")
        output.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    print("Created private application configuration.")


def provision() -> None:
    from app.config import Settings

    db = DatabaseSettings()
    app = Settings()
    if not db.test_admin_database_url or not app.runtime_database_url or not app.auth_database_url:
        raise RuntimeError("Run local database/application configuration first")
    admin = make_url(db.test_admin_database_url.get_secret_value())
    if (
        admin.host not in {"127.0.0.1", "localhost"}
        or admin.database != "cgl_dev"
        or admin.username != "cgl_admin"
    ):
        raise RuntimeError(
            "Role provisioning is restricted to the local cgl_dev bootstrap administrator"
        )
    try:
        with psycopg.connect(
            admin.set(drivername="postgresql").render_as_string(hide_password=False)
        ) as connection:
            for role, value in [
                ("cgl_runtime", app.runtime_database_url),
                ("cgl_auth", app.auth_database_url),
            ]:
                url = make_url(value.get_secret_value())
                exists = connection.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
                ).fetchone()
                if not exists:
                    connection.execute(
                        sql.SQL(
                            "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                        ).format(sql.Identifier(role))
                    )
                connection.execute(
                    sql.SQL(
                        "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}"
                    ).format(sql.Identifier(role), sql.Literal(url.password))
                )
            connection.execute("GRANT CONNECT ON DATABASE cgl_dev TO cgl_runtime, cgl_auth")
    except psycopg.Error:
        raise RuntimeError(
            "Local database role provisioning failed; verify the private configuration"
        ) from None
    print("Restricted local runtime/authentication roles provisioned.")


if __name__ == "__main__":
    import sys

    configure() if len(sys.argv) < 2 or sys.argv[1] == "configure" else provision()
