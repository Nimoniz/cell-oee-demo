"""Entry point: ``python -m app.api``."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import uvicorn


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = uvicorn.Config(
        "app.api.app:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 (inside a container, on a dedicated network)
        port=int(os.environ.get("PORT", "8000")),
        log_level="info",
    )
    server = uvicorn.Server(config)
    # uvicorn.run() would reinstate the Proactor loop policy on Windows (for subprocess
    # support); driving Server.serve() ourselves keeps the Selector loop psycopg's async mode
    # needs. Same fix as the collector's own entry point.
    if sys.platform == "win32":
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
