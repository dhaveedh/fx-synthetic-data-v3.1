"""
Service handler for FX data generation.
Wraps the generator and returns both the DataFrame and distribution tracker.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .generator import generate_fx_data, save_trades_to_csv, save_trades_chunked, DistributionTracker


def handle_generate_fx_data(
    rules: dict[str, Any],
    reference_data: dict[str, Any],
    seed: int | None = 42,
    progress_cb=None,
) -> tuple[pd.DataFrame, DistributionTracker]:
    """
    Generate FX trade data and return results with distribution tracking.

    Returns
    -------
    tuple[pd.DataFrame, DistributionTracker]
    """
    df, tracker = generate_fx_data(rules, reference_data, seed=seed, progress_cb=progress_cb)
    return df, tracker


def handle_generate_and_save(
    rules: dict[str, Any],
    reference_data: dict[str, Any],
    seed: int | None = 42,
    filename: str = "trades.csv",
    chunked: bool = False,
    progress_cb=None,
) -> tuple[pd.DataFrame, DistributionTracker, str]:
    """
    Generate, save, and return the DataFrame + tracker + file path.
    Uses chunked writer for volumes > 50k.
    """
    df, tracker = generate_fx_data(rules, reference_data, seed=seed, progress_cb=progress_cb)

    if chunked or len(df) > 50_000:
        path = save_trades_chunked(df, filename)
    else:
        path = save_trades_to_csv(df, filename)

    return df, tracker, path
