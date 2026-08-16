"""
Schema loader tests — dependency ordering and validation.
"""

import json

import pytest

from services.generate_fx_data.schema_loader import FieldSpec, TableSchema, load_table_schema


def test_default_trades_schema_loads():
    schema = load_table_schema("data/schema/trades.schema.json")
    assert "trade_id" in schema.column_order
    assert "notional_ccy" in schema.column_order


def test_column_order_matches_declaration_order():
    schema = TableSchema(fields=[
        FieldSpec(name="a", kind="bernoulli", p=0.5),
        FieldSpec(name="b", kind="derived", fn="noop", depends_on=["a"]),
        FieldSpec(name="c", kind="lifecycle"),
    ])
    assert schema.column_order == ["a", "b", "c"]


def test_evaluation_order_respects_dependencies():
    schema = TableSchema(fields=[
        FieldSpec(name="c", kind="derived", fn="noop", depends_on=["a", "b"]),
        FieldSpec(name="a", kind="bernoulli", p=0.5),
        FieldSpec(name="b", kind="bernoulli", p=0.5),
    ])
    order = [f.name for f in schema.evaluation_order()]
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("c")


def test_lifecycle_fields_excluded_from_evaluation_order():
    schema = TableSchema(fields=[
        FieldSpec(name="a", kind="bernoulli", p=0.5),
        FieldSpec(name="trade_id", kind="lifecycle"),
    ])
    order = [f.name for f in schema.evaluation_order()]
    assert order == ["a"]


def test_missing_dependency_raises():
    schema = TableSchema(fields=[
        FieldSpec(name="a", kind="derived", fn="noop", depends_on=["ghost"]),
    ])
    with pytest.raises(ValueError, match="unknown field"):
        schema.evaluation_order()


def test_cycle_raises():
    schema = TableSchema(fields=[
        FieldSpec(name="a", kind="derived", fn="noop", depends_on=["b"]),
        FieldSpec(name="b", kind="derived", fn="noop", depends_on=["a"]),
    ])
    with pytest.raises(ValueError, match="Cycle"):
        schema.evaluation_order()


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_table_schema(tmp_path / "does_not_exist.schema.json")


def test_load_rejects_unknown_kind(tmp_path):
    path = tmp_path / "bad.schema.json"
    path.write_text(json.dumps({"fields": [{"name": "x", "kind": "not_a_real_kind"}]}))
    with pytest.raises(ValueError, match="unknown kind"):
        load_table_schema(path)
