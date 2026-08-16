"""Organic anomaly-injection configuration used by the field engine."""

from __future__ import annotations

from .rng_utils import SeededRNG


class EdgeCaseConfig:
    """
    Controls organic anomaly injection during generation.
    Anomalies are woven into the generation loop so they participate
    in the lifecycle engine and produce realistic trails.
    """

    def __init__(self, edge_cases: list[str] | None = None, rates: dict[str, float] | None = None):
        self.enabled = set(edge_cases or [])
        defaults = {
            "null_rates": 0.01,
            "extreme_values": 0.01,
            "future_dates": 0.005,
            "negative_notional": 0.005,
            "duplicate_submissions": 0.01,
            "mismatched_ccy": 0.005,
        }
        self.rates = {**defaults, **(rates or {})}

    def should_inject(self, case_name: str, rng: SeededRNG) -> bool:
        if case_name not in self.enabled:
            return False
        return rng.random() < self.rates.get(case_name, 0.01)
