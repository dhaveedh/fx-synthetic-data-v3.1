"""
Generic field engine — builds one trade's attribute dict from a TableSchema
instead of hand-written per-field Python (see schema_loader.py for the schema
shape and field_functions.py for the named derived/mutator functions).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .edge_cases import EdgeCaseConfig
from .field_functions import DERIVED_FIELDS, EDGE_CASE_MUTATORS, FieldContext
from .rng_utils import SeededRNG, choose_weighted
from .schema_loader import FieldSpec, TableSchema


def _resolve_path(d: dict[str, Any], dotted: str) -> Any:
    node = d
    for part in dotted.split("."):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node


def _base_value(
    spec: FieldSpec,
    rng: SeededRNG,
    rules: dict[str, Any],
    reference_data: dict[str, Any],
    ctx: FieldContext,
) -> Any:
    if spec.kind == "weighted_choice":
        distribution = _resolve_path(rules, spec.source)
        options = reference_data[spec.options_from.split(":", 1)[1]] if spec.options_from else list(distribution.keys())
        return choose_weighted(rng, options, distribution)

    if spec.kind == "reference_choice":
        return rng.choice(reference_data[spec.source])

    if spec.kind == "numeric_range":
        return round(rng.uniform(spec.min, spec.max), spec.round)

    if spec.kind == "bernoulli":
        return rng.random() < spec.p

    if spec.kind == "derived":
        return DERIVED_FIELDS[spec.fn](ctx)

    raise ValueError(f"Field {spec.name!r} has unsupported kind {spec.kind!r} for the field engine")


def build_row(
    schema: TableSchema,
    rng: SeededRNG,
    rules: dict[str, Any],
    reference_data: dict[str, Any],
    edge_cfg: EdgeCaseConfig,
    reference_date: datetime,
) -> dict[str, Any]:
    """
    Build the attribute dict for one trade (every field except the 5
    lifecycle-owned columns), evaluating fields in dependency order and
    applying any edge-case mutators declared on each field.
    """
    row: dict[str, Any] = {}
    ctx = FieldContext(row=row, rng=rng, rules=rules, reference_data=reference_data, reference_date=reference_date)

    for spec in schema.evaluation_order():
        value = _base_value(spec, rng, rules, reference_data, ctx)

        for edge_case_name, mutator_name in spec.edge_case_mutators.items():
            if edge_cfg.should_inject(edge_case_name, rng):
                value = EDGE_CASE_MUTATORS[mutator_name](ctx, value)

        row[spec.name] = value

    return row
