"""
FX Trade Lifecycle Generator (v3.1)
=====================================

Improvements over v3:
- Every column of the output table is declared in a JSON schema
  (data/schema/trades.schema.json) instead of hardcoded in this file —
  see schema_loader.py / field_engine.py / field_functions.py.
- generate_fx_data() accepts an optional schema_path to point the generator
  at a different table schema entirely.

Carried over from v3/v2:
- Edge cases injected organically during generation (not bolted on after)
- Chunked CSV writing for memory-efficient large-volume generation
- Temporal consistency enforced (strictly increasing timestamps per trade)
- Distribution drift tracking built into the generation loop
- Configurable anomaly rates per edge-case type
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Callable

import pandas as pd

from shared.models import TERMINAL_STATUSES

from .edge_cases import EdgeCaseConfig
from .field_engine import build_row
from .rng_utils import SeededRNG, choose_weighted
from .schema_loader import TableSchema, load_table_schema

# ── Config ───────────────────────────────────────────────────────────

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data"))
DEFAULT_SCHEMA_PATH = os.path.join(DATA_DIR, "schema", "trades.schema.json")

CHUNK_SIZE = 10_000

# Output column order — derived from the default trades schema, not hardcoded.
# save_trades_to_csv/save_trades_chunked always write this order; pass a
# different schema_path to generate_fx_data() to generate a different table
# shape (the resulting DataFrame's own column order will differ accordingly).
COLUMNS = load_table_schema(DEFAULT_SCHEMA_PATH).column_order


# ── Utilities ────────────────────────────────────────────────────────

_trade_id_counter: int = 0


def reset_trade_id_counter() -> None:
    """Reset counter at start of each generation run for determinism."""
    global _trade_id_counter
    _trade_id_counter = 0


def generate_trade_id(rng: SeededRNG, timestamp: datetime) -> str:
    """Collision-free trade ID: timestamp + sequential counter."""
    global _trade_id_counter
    _trade_id_counter += 1
    ts_part = timestamp.strftime("%Y%m%d%H%M%S")
    return f"G{ts_part}{_trade_id_counter:06}"


def load_transitions(data_dir: str = DATA_DIR) -> dict[str, list[str]]:
    path = os.path.join(data_dir, "trade_status_transitions.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing file: {path}")
    df = pd.read_csv(path)
    transition_map: dict[str, list[str]] = {}
    for _, row in df.iterrows():
        transition_map.setdefault(row["from_status"], []).append(row["to_status"])
    return transition_map


# ── Distribution tracker ─────────────────────────────────────────────

class DistributionTracker:
    """
    Tracks actual vs requested distributions during generation
    so the validation layer can report drift.
    """

    def __init__(self, requested_ccy: dict[str, float], requested_type: dict[str, float], requested_dir: dict[str, float] | None = None):
        self.requested_ccy = requested_ccy
        self.requested_type = requested_type
        self.requested_dir = requested_dir or {"Buy": 0.5, "Sell": 0.5}
        self.actual_ccy: dict[str, int] = {}
        self.actual_type: dict[str, int] = {}
        self.actual_dir: dict[str, int] = {}
        self.total = 0

    def record(self, currency: str, trade_type: str, direction: str) -> None:
        self.actual_ccy[currency] = self.actual_ccy.get(currency, 0) + 1
        self.actual_type[trade_type] = self.actual_type.get(trade_type, 0) + 1
        self.actual_dir[direction] = self.actual_dir.get(direction, 0) + 1
        self.total += 1

    def get_drift_report(self) -> dict[str, Any]:
        if self.total == 0:
            return {"currency_drift": {}, "type_drift": {}, "direction_drift": {}, "max_drift": 0.0}

        ccy_drift = {}
        for ccy, target in self.requested_ccy.items():
            actual = self.actual_ccy.get(ccy, 0) / self.total
            ccy_drift[ccy] = {"target": target, "actual": round(actual, 4), "drift": round(abs(actual - target), 4)}

        type_drift = {}
        for tt, target in self.requested_type.items():
            actual = self.actual_type.get(tt, 0) / self.total
            type_drift[tt] = {"target": target, "actual": round(actual, 4), "drift": round(abs(actual - target), 4)}

        dir_drift = {}
        for d, target in self.requested_dir.items():
            actual = self.actual_dir.get(d, 0) / self.total
            dir_drift[d] = {"target": target, "actual": round(actual, 4), "drift": round(abs(actual - target), 4)}

        all_drifts = [d["drift"] for d in list(ccy_drift.values()) + list(type_drift.values()) + list(dir_drift.values())]
        return {
            "currency_drift": ccy_drift,
            "type_drift": type_drift,
            "direction_drift": dir_drift,
            "max_drift": max(all_drifts) if all_drifts else 0.0,
        }


# ── Lifecycle engine ─────────────────────────────────────────────────

def generate_lifecycle(
    rng: SeededRNG,
    transition_map: dict[str, list[str]],
    match_probability: float,
    settlement_ratio: float,
) -> list[str]:
    lifecycle: list[str] = []
    current = "Accepted"

    while True:
        lifecycle.append(current)
        if current in TERMINAL_STATUSES:
            break
        if current not in transition_map:
            break

        next_options = [s for s in transition_map[current] if s != current]
        if current == "Unmatched":
            next_options = [s for s in next_options if s != "Unmatched"]
        if "Matched" in next_options and rng.random() > match_probability:
            next_options = [s for s in next_options if s != "Matched"]
        if "Settled" in next_options and rng.random() > settlement_ratio:
            next_options = [s for s in next_options if s != "Settled"]
        if not next_options:
            break

        current = rng.choice(next_options)

    return lifecycle


# ── Matching engine ──────────────────────────────────────────────────

class MatchingEngine:
    """Strict 1-to-1 matching with cross-version re-matching support."""

    def __init__(self) -> None:
        self._used_pairs: set[frozenset[str]] = set()
        self._pool: dict[str, bool] = {}

    def try_match(self, trade_id: str, rng: SeededRNG) -> str | None:
        candidates = [
            tid for tid in self._pool
            if tid != trade_id
            and frozenset({trade_id, tid}) not in self._used_pairs
        ]
        if not candidates:
            self._pool[trade_id] = True
            return None

        partner = rng.choice(candidates)
        self._used_pairs.add(frozenset({trade_id, partner}))
        self._pool.pop(partner, None)
        self._pool.pop(trade_id, None)
        return partner


# ── Trade attribute builder ──────────────────────────────────────────

def _build_trade_attrs(
    schema: TableSchema,
    rng: SeededRNG,
    rules: dict[str, Any],
    reference_data: dict[str, Any],
    edge_cfg: EdgeCaseConfig,
    reference_date: datetime,
) -> dict[str, Any]:
    """
    Build immutable trade attributes by walking the trades table schema.
    Edge cases are injected organically (via each field's edge_case_mutators)
    so they flow through the entire lifecycle.
    """
    return build_row(schema, rng, rules, reference_data, edge_cfg, reference_date)


def _build_backfill_trade(
    rng: SeededRNG,
    forced_field: str,
    forced_value: str,
    parties: list[str],
    counterparties: list[str],
    currencies: list[str],
    trade_type_dist: dict[str, float],
    direction_dist: dict[str, float],
    reference_date: datetime,
    venues: list[str],
    brokers: list[str],
    party_buy_bias: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Create a minimal single-version trade to guarantee coverage."""
    trade_id = generate_trade_id(rng, reference_date)
    currency = rng.choice(currencies)
    base_ccy, counter_ccy = (
        currency.split("/") if "/" in currency else (currency, "USD")
    )
    trade_type = rng.choice(list(trade_type_dist.keys()))
    spot_rate = round(rng.uniform(0.5, 1.5), 5)
    notional = round(rng.uniform(100_000, 1_000_000), 2)

    party = rng.choice(parties)
    if party_buy_bias and party in party_buy_bias:
        direction = "Buy" if rng.random() < party_buy_bias[party] else "Sell"
    else:
        direction = choose_weighted(rng, list(direction_dist.keys()), direction_dist)

    row = {
        "trade_id": trade_id,
        "version": 1,
        "status": "Accepted",
        "party": party,
        "counterparty": rng.choice(counterparties),
        "currency": currency,
        "base_ccy": base_ccy,
        "counter_ccy": counter_ccy,
        "trade_type": trade_type,
        "tenor": 0,
        "notional": notional,
        "notional_ccy": round(spot_rate * notional, 2),
        "trade_date": reference_date.date(),
        "settlement_date": reference_date.date() + timedelta(days=2),
        "value_date": reference_date.date() + timedelta(days=2),
        "spot_rate": spot_rate,
        "forward_points": 0.0,
        "direction": direction,
        "fx_option_flag": rng.random() < 0.1,
        "trade_venue": rng.choice(venues),
        "broker": rng.choice(brokers),
        "matched_trade_id": None,
        "last_action_tms": reference_date,
    }
    row[forced_field] = forced_value
    return row


# ── Main generator ───────────────────────────────────────────────────

def generate_fx_data(
    rules: dict[str, Any],
    reference_data: dict[str, Any],
    seed: int | None = 42,
    progress_cb: Callable[[int, int], None] | None = None,
    schema_path: str | os.PathLike | None = None,
) -> tuple[pd.DataFrame, DistributionTracker]:
    """
    Generate a DataFrame of synthetic FX trade lifecycle records.

    Parameters
    ----------
    schema_path : optional
        Path to a trades table schema (see schema_loader.py). Defaults to
        data/schema/trades.schema.json — pass a different path to point the
        generator at an alternate table definition.

    Returns
    -------
    tuple[pd.DataFrame, DistributionTracker]
        The trades DataFrame and a tracker reporting distribution drift.
    """
    schema = load_table_schema(schema_path or DEFAULT_SCHEMA_PATH)

    rng = SeededRNG(seed)
    reset_trade_id_counter()

    num_trades = rules.get("volume", 1000)
    match_probability = rules.get("match_probability", 0.3)
    settlement_ratio = rules.get("settlement_ratio", 0.5)

    currencies = reference_data["currencies"]
    parties = reference_data["parties"]
    counterparties = reference_data["counterparties"]
    venues = reference_data.get("venues", ["EBS", "Reuters", "Bloomberg", "Currenex"])
    brokers = reference_data.get("brokers", ["BrokerA", "BrokerB", "BrokerC", "BrokerD"])
    party_buy_bias = reference_data.get("party_buy_bias", {})

    currency_dist = rules.get("currency_config", {}).get("distribution", {})
    trade_type_dist = rules.get("trade_type_distribution", {})
    direction_dist = rules.get("direction_distribution", {"Buy": 0.5, "Sell": 0.5})

    edge_cfg = EdgeCaseConfig(
        edge_cases=rules.get("edge_cases", []),
        rates=rules.get("edge_case_rates", {}),
    )

    transition_map = load_transitions()
    matcher = MatchingEngine()
    tracker = DistributionTracker(currency_dist, trade_type_dist, direction_dist)

    reference_date_str = rules.get("reference_date")
    reference_date = (
        datetime.fromisoformat(reference_date_str)
        if reference_date_str
        else datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    )
    single_day = bool(rules.get("single_day", False))

    rows: list[dict[str, Any]] = []
    _progress_interval = max(1, num_trades // 100)

    for trade_i in range(num_trades):
        if progress_cb and trade_i % _progress_interval == 0:
            progress_cb(trade_i, num_trades)
        base_timestamp = reference_date if single_day else reference_date - timedelta(days=rng.randint(0, 5))
        current_time = base_timestamp

        trade_id = generate_trade_id(rng, base_timestamp)

        attrs = _build_trade_attrs(schema, rng, rules, reference_data, edge_cfg, reference_date)

        tracker.record(attrs["currency"], attrs["trade_type"], attrs["direction"])

        duplicate_accepted = edge_cfg.should_inject("duplicate_submissions", rng)

        # Walk the state machine step-by-step so match downgrades
        # (Matched → Unmatched when no partner) feed into the next step correctly.
        current_status = "Accepted"
        version = 0

        while True:
            version += 1
            current_time += timedelta(minutes=rng.randint(1, 30))

            actual_status = current_status
            matched_trade_id: str | None = None

            if current_status == "Matched":
                partner = matcher.try_match(trade_id, rng)
                if partner is not None:
                    matched_trade_id = partner
                else:
                    # No partner available — don't emit a duplicate Unmatched row.
                    # The previous version already recorded this trade as Unmatched.
                    break

            row = {
                "trade_id": trade_id,
                **attrs,
                "version": version,
                "status": actual_status,
                "matched_trade_id": matched_trade_id,
                "last_action_tms": current_time,
            }
            rows.append(row)

            # Organic duplicate: re-submit Accepted with slightly later timestamp
            # Uses a separate version number so temporal ordering is clean
            if duplicate_accepted and version == 1 and actual_status == "Accepted":
                version += 1
                dup = {**row, "version": version}
                dup["last_action_tms"] = current_time + timedelta(seconds=rng.randint(1, 10))
                current_time = dup["last_action_tms"]
                rows.append(dup)

            # Check if we've reached a terminal state or dead end
            if actual_status in TERMINAL_STATUSES:
                break
            if actual_status not in transition_map:
                break

            # Pick the next status based on the ACTUAL emitted status
            next_options = [s for s in transition_map[actual_status] if s != actual_status]
            if actual_status == "Unmatched":
                next_options = [s for s in next_options if s != "Unmatched"]
            if "Matched" in next_options and rng.random() > match_probability:
                next_options = [s for s in next_options if s != "Matched"]
            if "Settled" in next_options and rng.random() > settlement_ratio:
                next_options = [s for s in next_options if s != "Settled"]
            if not next_options:
                break

            current_status = rng.choice(next_options)

    if progress_cb:
        progress_cb(num_trades, num_trades)

    # ── Backfill coverage ──
    existing_parties = {r["party"] for r in rows}
    existing_counterparties = {r["counterparty"] for r in rows}

    for party in parties:
        if party not in existing_parties:
            rows.append(_build_backfill_trade(
                rng, "party", party,
                parties, counterparties, currencies,
                trade_type_dist, direction_dist, reference_date, venues, brokers,
                party_buy_bias,
            ))

    for cp in counterparties:
        if cp not in existing_counterparties:
            rows.append(_build_backfill_trade(
                rng, "counterparty", cp,
                parties, counterparties, currencies,
                trade_type_dist, direction_dist, reference_date, venues, brokers,
                party_buy_bias,
            ))

    df = pd.DataFrame(rows).reindex(columns=schema.column_order)
    return df.reset_index(drop=True), tracker


# ── Save helpers ─────────────────────────────────────────────────────

def _resolve_output_path(filename: str) -> str:
    """
    Resolve `filename` against DATA_DIR and reject anything that would
    escape it (path separators, '..', drive-absolute paths, etc.) — a
    filesystem-level backstop regardless of how `filename` was constructed
    upstream (API request, agent-parsed config, CLI, ...).
    """
    base = os.path.realpath(DATA_DIR)
    candidate = os.path.realpath(os.path.join(DATA_DIR, filename))
    try:
        if os.path.commonpath([base, candidate]) != base:
            raise ValueError
    except ValueError:
        raise ValueError(f"Refusing to write outside {DATA_DIR!r}: {filename!r}") from None
    return candidate


def save_trades_to_csv(df: pd.DataFrame, filename: str = "trades.csv") -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = _resolve_output_path(filename)
    df.to_csv(path, index=False, columns=COLUMNS)
    return path


def save_trades_chunked(df: pd.DataFrame, filename: str = "trades.csv") -> str:
    """Memory-efficient chunked writer for large volumes."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = _resolve_output_path(filename)

    for i, chunk_start in enumerate(range(0, len(df), CHUNK_SIZE)):
        chunk = df.iloc[chunk_start:chunk_start + CHUNK_SIZE]
        if i == 0:
            chunk.to_csv(path, index=False, columns=COLUMNS, mode="w")
        else:
            chunk.to_csv(path, index=False, columns=COLUMNS, mode="a", header=False)

    return path
