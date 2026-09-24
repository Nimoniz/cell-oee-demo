"""Seedable random streams, one per subsystem.

Each stream is derived from ``(seed, salt, name)`` so adding draws in one subsystem never
shifts the sequence of another, and a given seed always reproduces the same run.
"""

from __future__ import annotations

import random


class RngFactory:
    def __init__(self, seed: int, salt: str = "0") -> None:
        self._seed = seed
        self._salt = salt

    def stream(self, name: str) -> random.Random:
        # str seeds are hashed with SHA-512 by ``random``: stable across processes.
        return random.Random(f"{self._seed}:{self._salt}:{name}")
