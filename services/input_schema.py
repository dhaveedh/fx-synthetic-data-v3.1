"""
Input Schema Loader
====================

Reference-data CSVs in data/ are paired by filename convention with a schema
definition in data/schema/<name>.schema.json. Each schema file is a flat map
of role -> actual column name in that CSV, so callers never hardcode a literal
column name — they ask "which column plays the id role" instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class InputSchema:
    """Role -> column name mapping for one reference-data CSV."""

    file: str
    columns: dict[str, str] = field(default_factory=dict)
    active_value: Any = None

    def column(self, role: str) -> str:
        try:
            return self.columns[role]
        except KeyError:
            raise KeyError(
                f"Schema for {self.file!r} has no column mapped to role {role!r} "
                f"(available roles: {sorted(self.columns)})"
            ) from None

    def has_role(self, role: str) -> bool:
        return role in self.columns


def load_input_schema(path: Path | str) -> InputSchema:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Missing schema definition: {path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if "file" not in data or "columns" not in data:
        raise ValueError(f"Schema {path} must define 'file' and 'columns'")

    return InputSchema(
        file=data["file"],
        columns=data["columns"],
        active_value=data.get("active_value"),
    )


def load_schema_dir(schema_dir: Path | str, names: list[str]) -> dict[str, InputSchema]:
    """Load one InputSchema per name, from <schema_dir>/<name>.schema.json."""
    schema_dir = Path(schema_dir)
    return {name: load_input_schema(schema_dir / f"{name}.schema.json") for name in names}
