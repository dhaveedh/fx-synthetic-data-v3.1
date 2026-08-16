"""
Reference Data Service (v3.1)
==============================

Loads reference data from CSV files and provides a clean interface
for the generator and orchestrator. Supports filtering by status
so only active parties/counterparties are used in generation.

v3.1: column names are no longer hardcoded. Each CSV in data_dir is paired
with a schema definition in data/schema/<name>.schema.json (see
services/input_schema.py) that maps a role (id, status_filter, weight, ...)
to the actual column name — so this service asks "which column is the id
column for parties" instead of writing df["party_id"] directly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

from services.input_schema import InputSchema, load_schema_dir

DATA_DIR = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "../data")))

INPUT_NAMES = [
    "parties", "counterparties", "currencies", "venues",
    "trade_types", "trade_lifecycle", "trade_status_transitions",
]


class ReferenceDataService:

    def __init__(self, data_dir: Path | str | None = None, schema_dir: Path | str | None = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.schema_dir = Path(schema_dir) if schema_dir else self.data_dir / "schema"
        self._schemas: dict[str, InputSchema] = load_schema_dir(self.schema_dir, INPUT_NAMES)

        self._currencies_df = pd.read_csv(self.data_dir / "currencies.csv")
        self._parties_df = pd.read_csv(self.data_dir / "parties.csv")
        self._counterparties_df = pd.read_csv(self.data_dir / "counterparties.csv")
        self._venues_df = pd.read_csv(self.data_dir / "venues.csv")
        self._trade_types_df = pd.read_csv(self.data_dir / "trade_types.csv")
        self._lifecycle_df = pd.read_csv(self.data_dir / "trade_lifecycle.csv")
        self._transitions_df = pd.read_csv(self.data_dir / "trade_status_transitions.csv")

    def _active_only(self, df: pd.DataFrame, schema_name: str, active_only: bool) -> pd.DataFrame:
        if not active_only:
            return df
        schema = self._schemas[schema_name]
        status_col = schema.column("status_filter")
        return df[df[status_col] == schema.active_value]

    # ── Currency pairs ───────────────────────────────────────────

    def get_currency_codes(self) -> list[str]:
        col = self._schemas["currencies"].column("id")
        return self._currencies_df[col].tolist()

    def get_currency_pairs(self) -> list[str]:
        codes = self.get_currency_codes()
        return [f"{a}/{b}" for a in codes for b in codes if a != b]

    def get_default_currency_pairs(self) -> list[str]:
        """
        FX pairs in market-convention order.

        Convention priority: EUR > GBP > AUD > NZD > USD > CAD > CHF > JPY
        EM/minor currencies quote against USD with USD first.
        """
        CONVENTIONAL_PAIRS = [
            # G10 majors
            "EUR/USD", "USD/JPY", "GBP/USD", "AUD/USD", "USD/CAD",
            "USD/CHF", "NZD/USD",
            # G10 crosses
            "EUR/GBP", "EUR/JPY", "EUR/CHF", "EUR/AUD", "EUR/CAD",
            "EUR/NZD", "EUR/NOK", "EUR/SEK",
            "GBP/JPY", "GBP/CHF", "GBP/AUD", "GBP/CAD", "GBP/NZD",
            "AUD/JPY", "AUD/NZD", "AUD/CAD", "AUD/CHF",
            "NZD/JPY", "NZD/CAD", "NZD/CHF",
            "CAD/JPY", "CAD/CHF",
            "CHF/JPY",
            # Scandis
            "USD/NOK", "USD/SEK", "NOK/SEK",
            # EM / minor
            "USD/KRW", "USD/HKD", "USD/SGD", "USD/ILS", "USD/MXN", "USD/CNH",
        ]
        codes = set(self.get_currency_codes())
        return [
            pair for pair in CONVENTIONAL_PAIRS
            if pair.split("/")[0] in codes and pair.split("/")[1] in codes
        ]

    # ── Parties ──────────────────────────────────────────────────

    def get_parties(self, active_only: bool = True) -> list[str]:
        df = self._active_only(self._parties_df, "parties", active_only)
        col = self._schemas["parties"].column("id")
        return df[col].tolist()

    def get_party_details(self) -> list[dict[str, str]]:
        return self._parties_df.to_dict("records")

    # ── Counterparties ───────────────────────────────────────────

    def get_counterparties(self, active_only: bool = True) -> list[str]:
        df = self._active_only(self._counterparties_df, "counterparties", active_only)
        col = self._schemas["counterparties"].column("id")
        return df[col].tolist()

    def get_counterparty_details(self) -> list[dict[str, str]]:
        return self._counterparties_df.to_dict("records")

    # ── Venues & Brokers ─────────────────────────────────────────

    def get_venues(self) -> list[str]:
        col = self._schemas["venues"].column("id")
        return self._venues_df[col].tolist()

    def get_brokers(self) -> list[str]:
        """Default brokers — no backing CSV/schema; extend here if needed."""
        return ["BrokerA", "BrokerB", "BrokerC", "BrokerD"]

    # ── Trade types ──────────────────────────────────────────────

    def get_trade_types(self) -> list[str]:
        col = self._schemas["trade_types"].column("id")
        return self._trade_types_df[col].tolist()

    # ── Lifecycle ────────────────────────────────────────────────

    def get_lifecycle_statuses(self) -> list[dict[str, Any]]:
        return self._lifecycle_df.to_dict("records")

    def get_transitions(self) -> dict[str, list[str]]:
        schema = self._schemas["trade_status_transitions"]
        from_col = schema.column("from")
        to_col = schema.column("to")
        transition_map: dict[str, list[str]] = {}
        for _, row in self._transitions_df.iterrows():
            transition_map.setdefault(row[from_col], []).append(row[to_col])
        return transition_map

    # ── Generator-compatible output ──────────────────────────────

    def get_party_buy_bias(self, active_only: bool = True) -> dict[str, float]:
        """Return {party_id: buy_bias} for direction generation."""
        schema = self._schemas["parties"]
        df = self._active_only(self._parties_df, "parties", active_only)
        if not schema.has_role("weight"):
            return {}
        id_col = schema.column("id")
        weight_col = schema.column("weight")
        return dict(zip(df[id_col], df[weight_col]))

    def get_counterparty_buy_bias(self, active_only: bool = True) -> dict[str, float]:
        """Return {counterparty_id: buy_bias} for direction generation."""
        schema = self._schemas["counterparties"]
        df = self._active_only(self._counterparties_df, "counterparties", active_only)
        if not schema.has_role("weight"):
            return {}
        id_col = schema.column("id")
        weight_col = schema.column("weight")
        return dict(zip(df[id_col], df[weight_col]))

    def get_reference_data(self, active_only: bool = True) -> dict[str, Any]:
        """
        Return a dict that can be passed directly to generate_fx_data().
        """
        return {
            "currencies": self.get_default_currency_pairs(),
            "parties": self.get_parties(active_only=active_only),
            "counterparties": self.get_counterparties(active_only=active_only),
            "venues": self.get_venues(),
            "brokers": self.get_brokers(),
            "party_buy_bias": self.get_party_buy_bias(active_only=active_only),
        }

    def summary(self) -> dict[str, Any]:
        """Quick summary for logging / agent context."""
        return {
            "currencies": len(self._currencies_df),
            "currency_pairs": len(self.get_default_currency_pairs()),
            "parties": len(self._parties_df),
            "active_parties": len(self.get_parties(active_only=True)),
            "counterparties": len(self._counterparties_df),
            "active_counterparties": len(self.get_counterparties(active_only=True)),
            "venues": len(self._venues_df),
            "trade_types": self.get_trade_types(),
        }
