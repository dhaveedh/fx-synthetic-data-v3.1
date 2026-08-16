"""
Generator tests — core invariants that must hold for every run.
"""

import pytest
from services.generate_fx_data.generator import generate_fx_data
from services.reference_data_service import ReferenceDataService

REFERENCE_DATA = ReferenceDataService().get_reference_data()

BASE_RULES = {
    "volume": 200,
    "seed": 42,
    "match_probability": 0.4,
    "settlement_ratio": 0.5,
    "currency_config": {"distribution": {"EUR/USD": 0.6, "GBP/USD": 0.4}},
    "trade_type_distribution": {"Spot": 0.7, "Forward": 0.2, "Swap": 0.1},
    "direction_distribution": {"Buy": 0.5, "Sell": 0.5},
    "edge_cases": [],
    "edge_case_rates": {},
}

TERMINAL = {"Settled", "Cancelled", "Rejected"}
OPEN = {"Accepted", "Unmatched", "Matched"}


def test_determinism():
    """Same seed must always produce byte-identical output."""
    df1, _ = generate_fx_data(BASE_RULES, REFERENCE_DATA, seed=42)
    df2, _ = generate_fx_data(BASE_RULES, REFERENCE_DATA, seed=42)
    assert df1.equals(df2), "Same seed produced different output"


def test_different_seeds_produce_different_output():
    df1, _ = generate_fx_data(BASE_RULES, REFERENCE_DATA, seed=42)
    df2, _ = generate_fx_data(BASE_RULES, REFERENCE_DATA, seed=99)
    assert not df1.equals(df2)


def test_open_trades_within_expected_range():
    """
    Some trades legitimately end in open states (Accepted, Unmatched) — that's
    realistic for a snapshot dataset. But the proportion should be broadly consistent
    with match_probability. With match_probability=0.4 we expect most trades to
    at least attempt matching; fewer than 70% should remain permanently open.
    """
    df, _ = generate_fx_data(BASE_RULES, REFERENCE_DATA, seed=42)
    last_status = df.sort_values("version").groupby("trade_id")["status"].last()
    open_ratio = last_status.isin(OPEN).mean()
    assert open_ratio < 0.70, (
        f"Too many trades ended in open state ({open_ratio:.0%}); "
        "may indicate a generator bug"
    )


def test_null_rates_propagate_to_notional_ccy():
    """When spot_rate is None, notional_ccy must also be None."""
    rules = {**BASE_RULES, "edge_cases": ["null_rates"], "edge_case_rates": {"null_rates": 1.0}}
    df, _ = generate_fx_data(rules, REFERENCE_DATA, seed=42)

    null_rate_rows = df[df["spot_rate"].isna()]
    assert len(null_rate_rows) > 0, "Expected null-rate rows but found none"
    assert null_rate_rows["notional_ccy"].isna().all(), (
        "notional_ccy should be None when spot_rate is None"
    )


def test_non_null_rates_have_notional_ccy():
    """When spot_rate is present, notional_ccy must also be present."""
    df, _ = generate_fx_data(BASE_RULES, REFERENCE_DATA, seed=42)
    real_rate_rows = df[df["spot_rate"].notna()]
    assert real_rate_rows["notional_ccy"].notna().all(), (
        "notional_ccy should not be None when spot_rate is present"
    )


def test_reference_date_respected():
    """Passing an explicit reference_date should anchor all trade dates near it."""
    rules = {**BASE_RULES, "reference_date": "2023-01-15T09:00:00"}
    df, _ = generate_fx_data(rules, REFERENCE_DATA, seed=42)
    import pandas as pd
    dates = pd.to_datetime(df["trade_date"])
    # All trade dates should be within 730 days of the reference date
    # (the future_dates edge case can go up to +730 days)
    assert (dates.dt.year >= 2022).all(), "Trade dates should be anchored to 2023 reference date"
    assert (dates.dt.year <= 2025).all(), "Trade dates should be anchored to 2023 reference date"


def test_all_versions_start_at_one():
    """Every trade must have a version 1 row."""
    df, _ = generate_fx_data(BASE_RULES, REFERENCE_DATA, seed=42)
    v1_trades = set(df[df["version"] == 1]["trade_id"])
    all_trades = set(df["trade_id"])
    missing = all_trades - v1_trades
    assert not missing, f"{len(missing)} trades are missing version 1"


def test_timestamps_strictly_increasing_per_trade():
    """last_action_tms must increase monotonically within each trade's lifecycle."""
    import pandas as pd
    df, _ = generate_fx_data(BASE_RULES, REFERENCE_DATA, seed=42)
    df = df.sort_values(["trade_id", "version"]).copy()
    df["_ts"] = pd.to_datetime(df["last_action_tms"])
    df["_prev_ts"] = df.groupby("trade_id")["_ts"].shift(1)
    violations = df[
        df["_prev_ts"].notna()
        & (df["_ts"] <= df["_prev_ts"])
        & (df["trade_id"] == df["trade_id"].shift(1))
    ]
    assert len(violations) == 0, (
        f"{violations['trade_id'].nunique()} trades have non-increasing timestamps"
    )
