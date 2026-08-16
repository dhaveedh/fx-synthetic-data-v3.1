"""
Trade table schema loader
===========================

Loads a JSON field-list schema (default: data/schema/trades.schema.json) that
describes every column of the generated trades table, and derives evaluation
order for the field engine via a topological sort on each field's
dependencies.

Field `kind`s:
  lifecycle        - filled by the state-machine loop in generator.py, not by
                      the field engine (trade_id, version, status,
                      matched_trade_id, last_action_tms)
  weighted_choice   - pick from a distribution at a dotted path into `rules`
  reference_choice  - uniform pick from a list in `reference_data`
  numeric_range     - rng.uniform(min, max) rounded to `round` decimals
  bernoulli         - rng.random() < p
  derived           - computed from already-built fields via a named
                      function in field_functions.DERIVED_FIELDS

`depends_on` is NOT written in the schema file — for `kind: derived` fields it
is inferred automatically by statically scanning the registered function's
source for `ctx.row["some_field"]` / `ctx.row.get("some_field")` reads (see
`_infer_row_dependencies`). This avoids a second, hand-maintained copy of
information the function body already encodes, which could silently drift out
of sync with what the function actually reads.

A field may declare `edge_cases: [edge_case_name, ...]` — each name is
resolved to a mutator via `DEFAULT_EDGE_CASE_MUTATOR` below (every edge case
in this project maps to exactly one canonical mutator today, so the schema
doesn't need to spell out the mapping per field).

The schema's own field order is the *output column order* (what COLUMNS /
REQUIRED_COLUMNS derive from). Evaluation order is computed separately via
topological sort so declaration order can stay human-readable.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import field_functions

VALID_KINDS = frozenset({
    "lifecycle", "weighted_choice", "reference_choice",
    "numeric_range", "bernoulli", "derived",
})

# Every edge case used in data/schema/trades.schema.json maps to exactly one
# mutator today. If a field ever needs a different mutator for the same edge
# case, extend this table (or the field's kind) rather than reintroducing a
# per-field mapping.
DEFAULT_EDGE_CASE_MUTATOR: dict[str, str] = {
    "null_rates": "set_none",
    "extreme_values": "multiply_extreme",
    "negative_notional": "negate",
    "mismatched_ccy": "random_bad_ccy",
    "future_dates": "shift_to_future",
}


def _infer_row_dependencies(fn) -> list[str]:
    """
    Statically scan a derived-field function's source for `ctx.row["x"]` /
    `ctx.row.get("x", ...)` reads and return the referenced field names, in
    first-seen order.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    seen: set[str] = set()
    deps: list[str] = []

    def _is_ctx_row(node: ast.AST) -> bool:
        return isinstance(node, ast.Attribute) and node.attr == "row"

    def _record(name: str) -> None:
        if name not in seen:
            seen.add(name)
            deps.append(name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _is_ctx_row(node.value):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                _record(node.slice.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_ctx_row(node.func.value)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            _record(node.args[0].value)

    return deps


@dataclass
class FieldSpec:
    name: str
    kind: str
    source: str | None = None
    options_from: str | None = None
    fn: str | None = None
    min: float | None = None
    max: float | None = None
    round: int | None = None
    p: float | None = None
    depends_on: list[str] = field(default_factory=list)
    edge_case_mutators: dict[str, str] = field(default_factory=dict)


@dataclass
class TableSchema:
    fields: list[FieldSpec]

    @property
    def column_order(self) -> list[str]:
        """Output column order — exactly as declared in the schema file."""
        return [f.name for f in self.fields]

    @property
    def lifecycle_field_names(self) -> set[str]:
        return {f.name for f in self.fields if f.kind == "lifecycle"}

    @property
    def attribute_fields_by_name(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields if f.kind != "lifecycle"}

    def evaluation_order(self) -> list[FieldSpec]:
        """
        Topologically sort attribute fields (everything except kind=lifecycle)
        by `depends_on`, using Kahn's algorithm with declaration order as the
        tie-break so results are stable and reproducible.
        """
        by_name = self.attribute_fields_by_name
        for f in by_name.values():
            for dep in f.depends_on:
                if dep not in by_name:
                    raise ValueError(
                        f"Field {f.name!r} depends_on unknown field {dep!r} "
                        f"(must be another non-lifecycle field name)"
                    )

        remaining = list(by_name.values())  # declaration order = tie-break order
        resolved: set[str] = set()
        ordered: list[FieldSpec] = []

        while remaining:
            ready = [f for f in remaining if all(dep in resolved for dep in f.depends_on)]
            if not ready:
                stuck = ", ".join(f.name for f in remaining)
                raise ValueError(f"Cycle (or unresolved dependency) detected among fields: {stuck}")
            for f in ready:
                ordered.append(f)
                resolved.add(f.name)
            remaining = [f for f in remaining if f.name not in resolved]

        return ordered


def _parse_field(raw: dict[str, Any]) -> FieldSpec:
    if "name" not in raw or "kind" not in raw:
        raise ValueError(f"Field definition missing 'name' or 'kind': {raw}")
    name, kind = raw["name"], raw["kind"]
    if kind not in VALID_KINDS:
        raise ValueError(f"Field {name!r} has unknown kind {kind!r}")

    depends_on: list[str] = []
    if kind == "derived":
        fn_name = raw.get("fn")
        if fn_name not in field_functions.DERIVED_FIELDS:
            raise ValueError(
                f"Field {name!r} references unknown derived function {fn_name!r} "
                f"(not registered in field_functions.DERIVED_FIELDS)"
            )
        depends_on = _infer_row_dependencies(field_functions.DERIVED_FIELDS[fn_name])

    edge_case_mutators: dict[str, str] = {}
    for edge_case_name in raw.get("edge_cases", []):
        if edge_case_name not in DEFAULT_EDGE_CASE_MUTATOR:
            raise ValueError(
                f"Field {name!r} declares unknown edge case {edge_case_name!r} "
                f"(not in DEFAULT_EDGE_CASE_MUTATOR)"
            )
        edge_case_mutators[edge_case_name] = DEFAULT_EDGE_CASE_MUTATOR[edge_case_name]

    return FieldSpec(
        name=name,
        kind=kind,
        source=raw.get("source"),
        options_from=raw.get("options_from"),
        fn=raw.get("fn"),
        min=raw.get("min"),
        max=raw.get("max"),
        round=raw.get("round"),
        p=raw.get("p"),
        depends_on=depends_on,
        edge_case_mutators=edge_case_mutators,
    )


def load_table_schema(path: Path | str) -> TableSchema:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Missing table schema: {path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if "fields" not in data:
        raise ValueError(f"Schema {path} must define a 'fields' list")

    schema = TableSchema(fields=[_parse_field(raw) for raw in data["fields"]])
    schema.evaluation_order()  # validate at load time (raises on cycle/missing dep)
    return schema
