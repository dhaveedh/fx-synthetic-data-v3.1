"""
Named function registries referenced by data/schema/trades.schema.json.

DERIVED_FIELDS holds functions for `kind: derived` fields — computed from
fields already built earlier in the field engine's evaluation order.
EDGE_CASE_MUTATORS holds functions for a field's `edge_case_mutators` map —
each replaces the field's base value when the named edge case fires.

Ported 1:1 from the inline logic that used to live in generator.py's
_build_trade_attrs, so behavior is unchanged — only the wiring moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from .rng_utils import SeededRNG, choose_weighted


@dataclass
class FieldContext:
    """Everything a derived/mutator function needs to compute one field."""

    row: dict[str, Any]          # fields already built for this trade, in evaluation order
    rng: SeededRNG
    rules: dict[str, Any]
    reference_data: dict[str, Any]
    reference_date: datetime


DERIVED_FIELDS: dict[str, Callable[[FieldContext], Any]] = {}
EDGE_CASE_MUTATORS: dict[str, Callable[[FieldContext, Any], Any]] = {}


def derived(name: str):
    def register(fn: Callable[[FieldContext], Any]):
        DERIVED_FIELDS[name] = fn
        return fn
    return register


def mutator(name: str):
    def register(fn: Callable[[FieldContext, Any], Any]):
        EDGE_CASE_MUTATORS[name] = fn
        return fn
    return register


# ── Derived fields ───────────────────────────────────────────────────

@derived("split_base_ccy")
def _split_base_ccy(ctx: FieldContext) -> str:
    currency = ctx.row["currency"]
    return currency.split("/")[0] if "/" in currency else currency


@derived("split_counter_ccy")
def _split_counter_ccy(ctx: FieldContext) -> str:
    currency = ctx.row["currency"]
    return currency.split("/")[1] if "/" in currency else "USD"


@derived("random_tenor")
def _random_tenor(ctx: FieldContext) -> int:
    trade_type = ctx.row["trade_type"]
    if trade_type == "Spot":
        return 0
    elif trade_type == "Forward":
        return ctx.rng.choice([7, 14, 30, 90, 180])
    elif trade_type == "Swap":
        return ctx.rng.choice([30, 90, 180, 360])
    return 0


@derived("compute_notional_ccy")
def _compute_notional_ccy(ctx: FieldContext) -> float | None:
    spot_rate = ctx.row["spot_rate"]
    notional = ctx.row["notional"]
    return round(spot_rate * notional, 2) if spot_rate is not None else None


@derived("trade_date_near_reference")
def _trade_date_near_reference(ctx: FieldContext):
    if ctx.rules.get("single_day", False):
        return ctx.reference_date.date()
    return (ctx.reference_date - timedelta(days=ctx.rng.randint(0, 30))).date()


@derived("settlement_date_offset")
def _settlement_date_offset(ctx: FieldContext):
    return ctx.row["trade_date"] + timedelta(days=2)


@derived("value_date_offset")
def _value_date_offset(ctx: FieldContext):
    tenor = ctx.row["tenor"]
    return ctx.row["trade_date"] + timedelta(days=tenor if tenor > 0 else 2)


@derived("forward_points_for_type")
def _forward_points_for_type(ctx: FieldContext) -> float | None:
    spot_rate = ctx.row["spot_rate"]
    if spot_rate is None:
        return None
    trade_type = ctx.row["trade_type"]
    return round(ctx.rng.uniform(-0.01, 0.01), 5) if trade_type != "Spot" else 0.0


@derived("direction_for_party")
def _direction_for_party(ctx: FieldContext) -> str:
    party = ctx.row["party"]
    party_buy_bias = ctx.reference_data.get("party_buy_bias") or {}
    if party in party_buy_bias:
        return "Buy" if ctx.rng.random() < party_buy_bias[party] else "Sell"
    direction_dist = ctx.rules.get("direction_distribution", {"Buy": 0.5, "Sell": 0.5})
    return choose_weighted(ctx.rng, list(direction_dist.keys()), direction_dist)


# ── Edge-case mutators ───────────────────────────────────────────────

@mutator("random_bad_ccy")
def _random_bad_ccy(ctx: FieldContext, value: Any) -> str:
    return ctx.rng.choice(["XXX", "ZZZ", "QQQ"])


@mutator("multiply_extreme")
def _multiply_extreme(ctx: FieldContext, value: float) -> float:
    return value * ctx.rng.choice([100, 1000])


@mutator("negate")
def _negate(ctx: FieldContext, value: float) -> float:
    return -abs(value)


@mutator("set_none")
def _set_none(ctx: FieldContext, value: Any) -> None:
    return None


@mutator("shift_to_future")
def _shift_to_future(ctx: FieldContext, value: Any):
    return (ctx.reference_date + timedelta(days=ctx.rng.randint(365, 730))).date()
