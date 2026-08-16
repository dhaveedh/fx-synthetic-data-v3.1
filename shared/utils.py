"""
Utility functions for the FX Synthetic Trade Generator.
"""

from __future__ import annotations

from typing import Any


def enforce_max_volume(config: dict[str, Any]) -> None:
    """Validate volume is a positive integer."""
    vol = config.get("volume", 0)
    if vol < 1:
        raise ValueError("Volume must be at least 1")


def assign_default_seed(config: dict[str, Any]) -> dict[str, Any]:
    """Ensure a seed is present for deterministic generation."""
    if "seed" not in config:
        config["seed"] = 42
    return config


def normalise_distribution(dist: dict[str, float]) -> dict[str, float]:
    """
    Normalise distribution values to sum to 1.0.
    Handles cases where the agent returns values that don't quite sum.
    """
    total = sum(dist.values())
    if total == 0:
        n = len(dist)
        return {k: 1.0 / n for k in dist}
    return {k: v / total for k, v in dist.items()}


def sanitise_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    Apply all pre-generation sanitisation steps.
    Called by the orchestrator before passing to the generator.
    """
    config = assign_default_seed(config)
    enforce_max_volume(config)

    # Normalise distributions
    ccy_dist = config.get("currency_config", {}).get("distribution", {})
    if ccy_dist:
        config["currency_config"]["distribution"] = normalise_distribution(ccy_dist)

    tt_dist = config.get("trade_type_distribution", {})
    if tt_dist:
        config["trade_type_distribution"] = normalise_distribution(tt_dist)

    dir_dist = config.get("direction_distribution", {})
    if dir_dist:
        config["direction_distribution"] = normalise_distribution(dir_dist)

    return config
