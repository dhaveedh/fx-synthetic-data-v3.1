"""
Data models for the FX Synthetic Trade Generator pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VALID_STATUSES: frozenset[str] = frozenset({"Accepted", "Unmatched", "Matched", "Settled", "Cancelled", "Rejected"})
TERMINAL_STATUSES: frozenset[str] = frozenset({"Settled", "Cancelled", "Rejected"})
VALID_TRADE_TYPES: frozenset[str] = frozenset({"Spot", "Forward", "Swap", "Option"})
VALID_DIRECTIONS: frozenset[str] = frozenset({"Buy", "Sell"})
VALID_EDGE_CASES: frozenset[str] = frozenset({
    "null_rates", "extreme_values", "future_dates",
    "negative_notional", "duplicate_submissions", "mismatched_ccy",
})


@dataclass
class GenerationConfig:
    """Validated generation configuration."""

    volume: int = 1000
    seed: int = 42
    match_probability: float = 0.3
    settlement_ratio: float = 0.5
    currency_config: dict[str, Any] = field(default_factory=lambda: {
        "distribution": {"EUR/USD": 0.5, "GBP/USD": 0.3, "USD/JPY": 0.2}
    })
    trade_type_distribution: dict[str, float] = field(default_factory=lambda: {
        "Spot": 0.7, "Forward": 0.2, "Swap": 0.1
    })
    direction_distribution: dict[str, float] = field(default_factory=lambda: {
        "Buy": 0.5, "Sell": 0.5
    })
    edge_cases: list[str] = field(default_factory=list)
    edge_case_rates: dict[str, float] = field(default_factory=dict)
    scenario_name: str = "default"
    scenario_description: str = "Default generation scenario"
    reference_date: str | None = None  # ISO datetime e.g. "2025-06-01T09:00:00"; defaults to today at 09:00
    single_day: bool = False  # confine trade_date and last_action_tms's start to reference_date exactly

    def to_rules(self) -> dict[str, Any]:
        """Convert to the dict format expected by the generator."""
        return {
            "volume": self.volume,
            "seed": self.seed,
            "match_probability": self.match_probability,
            "settlement_ratio": self.settlement_ratio,
            "currency_config": self.currency_config,
            "trade_type_distribution": self.trade_type_distribution,
            "direction_distribution": self.direction_distribution,
            "edge_cases": self.edge_cases,
            "edge_case_rates": self.edge_case_rates,
            "reference_date": self.reference_date,
            "single_day": self.single_day,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GenerationConfig:
        """Create from a dict (e.g. parsed from agent JSON)."""
        return cls(
            volume=d.get("volume", 1000),
            seed=d.get("seed", 42),
            match_probability=d.get("match_probability", 0.3),
            settlement_ratio=d.get("settlement_ratio", 0.5),
            currency_config=d.get("currency_config", {"distribution": {"EUR/USD": 0.5, "GBP/USD": 0.3, "USD/JPY": 0.2}}),
            trade_type_distribution=d.get("trade_type_distribution", {"Spot": 0.7, "Forward": 0.2, "Swap": 0.1}),
            direction_distribution=d.get("direction_distribution", {"Buy": 0.5, "Sell": 0.5}),
            edge_cases=d.get("edge_cases", []),
            edge_case_rates=d.get("edge_case_rates", {}),
            scenario_name=d.get("scenario_name", "default"),
            scenario_description=d.get("scenario_description", ""),
            reference_date=d.get("reference_date", None),
            single_day=d.get("single_day", False),
        )

    def validate(self) -> list[str]:
        """Return a list of config validation errors (empty = valid)."""
        errors = []

        if self.volume < 500:
            errors.append("volume must be >= 500 (smaller samples produce unreliable distribution drift results)")
        if not 0.0 <= self.match_probability <= 1.0:
            errors.append("match_probability must be between 0 and 1")
        if not 0.0 <= self.settlement_ratio <= 1.0:
            errors.append("settlement_ratio must be between 0 and 1")

        ccy_dist = self.currency_config.get("distribution", {})
        if ccy_dist:
            total = sum(ccy_dist.values())
            if abs(total - 1.0) > 0.05:
                errors.append(f"currency distribution sums to {total:.2f}, expected ~1.0")

        if self.trade_type_distribution:
            total = sum(self.trade_type_distribution.values())
            if abs(total - 1.0) > 0.05:
                errors.append(f"trade_type distribution sums to {total:.2f}, expected ~1.0")

        if self.direction_distribution:
            total = sum(self.direction_distribution.values())
            if abs(total - 1.0) > 0.05:
                errors.append(f"direction distribution sums to {total:.2f}, expected ~1.0")
            invalid_dirs = set(self.direction_distribution.keys()) - VALID_DIRECTIONS
            if invalid_dirs:
                errors.append(f"Invalid directions: {invalid_dirs}")

        invalid = set(self.edge_cases) - VALID_EDGE_CASES
        if invalid:
            errors.append(f"Unknown edge cases: {invalid}")

        return errors


@dataclass
class PipelineResult:
    """Result of a full generation pipeline run."""

    config: GenerationConfig
    trades_path: str
    total_rows: int
    unique_trades: int
    validation_passed: bool
    validation_summary: str
    drift_report: dict[str, Any] = field(default_factory=dict)
    iteration: int = 1
