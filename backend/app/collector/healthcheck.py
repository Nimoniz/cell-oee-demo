"""Container healthcheck: the collector's main loop has ticked recently.

Being unable to reach the simulator is not unhealthy: reconnecting with backoff is the job.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from app.config import load_settings

MAX_AGE_S = 15.0


def main() -> int:
    path = Path(load_settings().collector.health_file)
    try:
        age = time.time() - float(path.read_text())
    except (OSError, ValueError):
        return 1
    return 0 if age < MAX_AGE_S else 1


if __name__ == "__main__":
    sys.exit(main())
