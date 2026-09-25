"""Write the JSON API's OpenAPI document (the contract the React client is typed from).

Usage:
    uv run python scripts/export_openapi.py frontend/openapi.json

Only the routers are mounted, so no database, browser or settings are needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI

from rma_portal.web.api import router


def build_document() -> dict:
    app = FastAPI(title="Portail RMA", version="2.0.0")
    app.include_router(router)
    return app.openapi()


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path("frontend/openapi.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_document(), indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"OpenAPI écrit dans {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
