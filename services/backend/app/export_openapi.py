"""Export the API contract without requiring local environment files or a running server."""

import json
import sys
from pathlib import Path

from app.config import Environment, Settings
from app.main import create_app


def main() -> None:
    settings = Settings(app_env=Environment.TEST, _env_file=None)
    schema = create_app(settings).openapi()
    destination = Path(sys.argv[1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
