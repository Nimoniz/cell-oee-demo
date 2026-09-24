"""Dump the API's OpenAPI schema to a file, without starting a server or touching the database.

Building the FastAPI app does not run its lifespan (migration, DB engine, live hub), so this is
safe to run offline — it is how the frontend generates its TypeScript types
(``frontend/package.json``'s ``generate:types`` script).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.app import create_app  # noqa: E402
from app.config import load_settings  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("openapi.json")
    app = create_app(load_settings())
    out.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
