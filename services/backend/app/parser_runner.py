"""One document per resource-limited, credential-free subprocess."""

import json
import resource
import sys
from pathlib import Path


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_FSIZE, (12 * 1024 * 1024, 12 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    from app.documents import DocumentError, parse_pdf, parse_xlsx

    data = Path("input").read_bytes()
    try:
        blocks, pages = parse_pdf(data) if sys.argv[1] == "application/pdf" else parse_xlsx(data)
        result = {
            "blocks": [{"text": block.text, "locator": block.locator} for block in blocks],
            "page_count": pages,
        }
    except DocumentError as error:
        result = {"error": str(error)}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
