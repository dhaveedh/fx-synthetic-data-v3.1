"""Seeded RNG wrapper and weighted-choice helper shared across the generator."""

from __future__ import annotations

import random as _stdlib_random
from typing import Any

import numpy as np


class SeededRNG:
    def __init__(self, seed: int | None = None) -> None:
        self.py = _stdlib_random.Random(seed)
        self.np = np.random.default_rng(seed)

    def choice(self, seq: list) -> Any:
        return self.py.choice(seq)

    def choices_weighted(self, population: list, weights: list[float]) -> Any:
        total = sum(weights)
        if total == 0:
            return self.py.choice(population)
        return self.py.choices(population, weights=weights, k=1)[0]

    def uniform(self, lo: float, hi: float) -> float:
        return self.py.uniform(lo, hi)

    def randint(self, lo: int, hi: int) -> int:
        return self.py.randint(lo, hi)

    def random(self) -> float:
        return self.py.random()


def choose_weighted(rng: SeededRNG, options: list[str], weights_dict: dict[str, float]) -> str:
    weights = [weights_dict.get(o, 0.0) for o in options]
    return rng.choices_weighted(options, weights)
