"""
Field engine tests — kind dispatch, dependency visibility, edge-case mutators.
"""

from datetime import datetime

from services.generate_fx_data.edge_cases import EdgeCaseConfig
from services.generate_fx_data.field_engine import build_row
from services.generate_fx_data.field_functions import DERIVED_FIELDS, EDGE_CASE_MUTATORS, derived, mutator
from services.generate_fx_data.rng_utils import SeededRNG
from services.generate_fx_data.schema_loader import FieldSpec, TableSchema, load_table_schema

REFERENCE_DATE = datetime(2024, 6, 15, 9, 0, 0)


def test_kind_dispatch_produces_expected_types():
    schema = TableSchema(fields=[
        FieldSpec(name="choice", kind="weighted_choice", source="dist"),
        FieldSpec(name="ref", kind="reference_choice", source="options"),
        FieldSpec(name="num", kind="numeric_range", min=1.0, max=1.0, round=2),
        FieldSpec(name="flag", kind="bernoulli", p=1.0),
    ])
    rules = {"dist": {"X": 1.0}}
    reference_data = {"options": ["only"]}
    row = build_row(schema, SeededRNG(1), rules, reference_data, EdgeCaseConfig(), REFERENCE_DATE)

    assert row["choice"] == "X"
    assert row["ref"] == "only"
    assert row["num"] == 1.0
    assert row["flag"] is True


def test_derived_field_sees_earlier_row_values():
    @derived("_test_double_num")
    def _double(ctx):
        return ctx.row["num"] * 2

    schema = TableSchema(fields=[
        FieldSpec(name="num", kind="numeric_range", min=5.0, max=5.0, round=1),
        FieldSpec(name="doubled", kind="derived", fn="_test_double_num", depends_on=["num"]),
    ])
    row = build_row(schema, SeededRNG(1), {}, {}, EdgeCaseConfig(), REFERENCE_DATE)
    assert row["doubled"] == 10.0
    del DERIVED_FIELDS["_test_double_num"]


def test_edge_case_mutator_only_fires_when_enabled_and_sampled():
    @mutator("_test_zero_out")
    def _zero(ctx, value):
        return 0.0

    schema = TableSchema(fields=[
        FieldSpec(
            name="num", kind="numeric_range", min=5.0, max=5.0, round=1,
            edge_case_mutators={"boom": "_test_zero_out"},
        ),
    ])

    # Edge case not enabled -> mutator never fires.
    off = build_row(schema, SeededRNG(1), {}, {}, EdgeCaseConfig(), REFERENCE_DATE)
    assert off["num"] == 5.0

    # Edge case enabled at rate 1.0 -> mutator always fires.
    on_cfg = EdgeCaseConfig(edge_cases=["boom"], rates={"boom": 1.0})
    on = build_row(schema, SeededRNG(1), {}, {}, on_cfg, REFERENCE_DATE)
    assert on["num"] == 0.0

    del EDGE_CASE_MUTATORS["_test_zero_out"]


def test_weighted_choice_options_from_reference_data():
    """currency-style field: candidates come from reference_data, weights from rules."""
    schema = TableSchema(fields=[
        FieldSpec(name="pair", kind="weighted_choice", source="dist", options_from="reference_data:pairs"),
    ])
    rules = {"dist": {"EUR/USD": 1.0}}
    reference_data = {"pairs": ["EUR/USD", "GBP/USD"]}
    row = build_row(schema, SeededRNG(1), rules, reference_data, EdgeCaseConfig(), REFERENCE_DATE)
    assert row["pair"] == "EUR/USD"


def test_null_rates_propagate_through_real_trades_schema():
    """Regression guard for the real trades.schema.json: null_rates -> notional_ccy/forward_points null."""
    schema = load_table_schema("data/schema/trades.schema.json")
    rules = {
        "currency_config": {"distribution": {"EUR/USD": 1.0}},
        "trade_type_distribution": {"Forward": 1.0},
        "direction_distribution": {"Buy": 0.5, "Sell": 0.5},
    }
    reference_data = {
        "currencies": ["EUR/USD"], "parties": ["P1"], "counterparties": ["C1"],
        "venues": ["V1"], "brokers": ["B1"], "party_buy_bias": {},
    }
    edge_cfg = EdgeCaseConfig(edge_cases=["null_rates"], rates={"null_rates": 1.0})
    row = build_row(schema, SeededRNG(1), rules, reference_data, edge_cfg, REFERENCE_DATE)

    assert row["spot_rate"] is None
    assert row["notional_ccy"] is None
    assert row["forward_points"] is None
