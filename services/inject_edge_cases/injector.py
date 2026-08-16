"""
Edge Case Injector (DEPRECATED)
================================

In v2, edge cases are injected organically during generation via
the EdgeCaseConfig class in generator.py. This module is retained
for backward compatibility only.

To inject edge cases, pass them in the rules dict:
    rules = {
        "edge_cases": ["null_rates", "extreme_values", "duplicate_submissions"],
        "edge_case_rates": {"null_rates": 0.02},  # optional rate overrides
        ...
    }
"""

from __future__ import annotations

import warnings
from typing import Any

import pandas as pd


def inject_edge_cases(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """
    DEPRECATED: Post-hoc edge case injection.

    In v2, edge cases are injected organically during generation.
    This function is retained for backward compatibility but will
    produce less realistic results than organic injection.

    Use the 'edge_cases' key in your generation rules instead.
    """
    warnings.warn(
        "inject_edge_cases() is deprecated. Edge cases are now injected "
        "organically during generation via the 'edge_cases' rules key. "
        "See generator.EdgeCaseConfig for details.",
        DeprecationWarning,
        stacklevel=2,
    )

    edge_cases = config.get("edge_cases", [])
    if not edge_cases:
        return df

    result = df.copy()

    if "null_rates" in edge_cases:
        sample_idx = result.sample(frac=0.01, random_state=99).index
        result.loc[sample_idx, "spot_rate"] = None

    if "duplicates" in edge_cases:
        dups = result.sample(frac=0.01, random_state=99)
        result = pd.concat([result, dups], ignore_index=True)

    if "extreme_values" in edge_cases:
        sample_idx = result.sample(frac=0.01, random_state=99).index
        result.loc[sample_idx, "notional"] *= 100

    return result
