"""Container healthcheck: the OPC UA port accepts TCP connections."""

from __future__ import annotations

import os
import socket
import sys
from urllib.parse import urlparse

from .config import load_config


def main() -> int:
    cfg = load_config(os.environ.get("CELL_SIM_CONFIG", "config.yaml"))
    port = urlparse(cfg.opcua.endpoint).port or 4840
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return 0
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
