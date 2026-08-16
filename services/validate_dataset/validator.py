"""
FX Trade Dataset Validator (v2)
================================

Deep validation with:
- Schema validation (column types, value ranges, enum membership)
- Referential integrity (matched_trade_id points to real trades)
- Temporal consistency (last_action_tms strictly increasing per trade)
- Distribution drift detection (actual vs requested within tolerance)
- Edge case detection and reporting
- Structured validation report for agent feedback loop
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from shared.models import VALID_STATUSES, VALID_TRADE_TYPES, VALID_DIRECTIONS
from services.generate_fx_data.generator import COLUMNS as REQUIRED_COLUMNS


# ── Validation result types ──────────────────────────────────────────

class ValidationIssue:
    """A single validation finding."""

    def __init__(
        self,
        severity: str,       # "error" | "warning" | "info"
        category: str,       # "schema" | "integrity" | "temporal" | "distribution" | "edge_case"
        field: str | None,
        message: str,
        count: int = 1,
        sample_ids: list[str] | None = None,
    ):
        self.severity = severity
        self.category = category
        self.field = field
        self.message = message
        self.count = count
        self.sample_ids = sample_ids or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "field": self.field,
            "message": self.message,
            "count": self.count,
            "sample_ids": self.sample_ids[:5],  # Cap for readability
        }


class ValidationReport:
    """Full validation report that can be fed back to the agent."""

    def __init__(self):
        self.issues: list[ValidationIssue] = []
        self.stats: dict[str, Any] = {}
        self.passed = True

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)
        if issue.severity == "error":
            self.passed = False

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [i.to_dict() for i in self.issues],
            "stats": self.stats,
        }

    def summary_for_agent(self) -> str:
        """
        Produce a concise text summary the agent can use to reason
        about whether to adjust the config and re-generate.
        """
        lines = [
            f"Validation {'PASSED' if self.passed else 'FAILED'}: "
            f"{self.error_count} errors, {self.warning_count} warnings",
        ]
        for i in self.issues:
            if i.severity in ("error", "warning"):
                lines.append(f"  [{i.severity.upper()}] {i.category}/{i.field}: {i.message}")
        if self.stats.get("drift_report"):
            dr = self.stats["drift_report"]
            if dr["max_drift"] > 0.05:
                lines.append(f"  [DRIFT] Max distribution drift: {dr['max_drift']:.2%}")
        return "\n".join(lines)


# ── Validators ───────────────────────────────────────────────────────
# REQUIRED_COLUMNS is imported from generator.COLUMNS above, which is itself
# derived from data/schema/trades.schema.json — a single source of truth for
# the output table shape, shared by the generator and this validator.


def _validate_schema(df: pd.DataFrame, report: ValidationReport) -> None:
    """Check columns exist, types are correct, enums are valid."""

    # Column presence
    missing = set(REQUIRED_COLUMNS) - set(df.columns)
    if missing:
        report.add(ValidationIssue("error", "schema", None, f"Missing columns: {missing}", len(missing)))
        return  # Can't continue without required columns

    # Empty dataset
    if df.empty:
        report.add(ValidationIssue("error", "schema", None, "Dataset is empty"))
        return

    # Enum membership: status
    invalid_status = df[~df["status"].isin(VALID_STATUSES)]
    if len(invalid_status):
        bad_vals = invalid_status["status"].unique().tolist()
        report.add(ValidationIssue(
            "error", "schema", "status",
            f"Invalid status values: {bad_vals}",
            len(invalid_status),
            invalid_status["trade_id"].head(5).tolist(),
        ))

    # Enum membership: trade_type
    invalid_tt = df[~df["trade_type"].isin(VALID_TRADE_TYPES)]
    if len(invalid_tt):
        bad_vals = invalid_tt["trade_type"].unique().tolist()
        report.add(ValidationIssue(
            "error", "schema", "trade_type",
            f"Invalid trade types: {bad_vals}",
            len(invalid_tt),
            invalid_tt["trade_id"].head(5).tolist(),
        ))

    # Enum membership: direction
    invalid_dir = df[~df["direction"].isin(VALID_DIRECTIONS)]
    if len(invalid_dir):
        report.add(ValidationIssue(
            "error", "schema", "direction",
            f"Invalid direction values found",
            len(invalid_dir),
        ))

    # Null spot rates
    null_rates = df[df["spot_rate"].isnull()]
    if len(null_rates):
        report.add(ValidationIssue(
            "warning", "schema", "spot_rate",
            f"{len(null_rates)} trades with null spot rates",
            len(null_rates),
            null_rates["trade_id"].head(5).tolist(),
        ))

    # Negative notional
    neg_notional = df[df["notional"] < 0]
    if len(neg_notional):
        report.add(ValidationIssue(
            "error", "schema", "notional",
            f"{len(neg_notional)} trades with negative notional",
            len(neg_notional),
            neg_notional["trade_id"].head(5).tolist(),
        ))

    # Notional range (extreme values)
    extreme = df[df["notional"].abs() > 100_000_000]
    if len(extreme):
        report.add(ValidationIssue(
            "warning", "schema", "notional",
            f"{len(extreme)} trades with extreme notional (>100M)",
            len(extreme),
            extreme["trade_id"].head(5).tolist(),
        ))

    # Future trade dates — flag anything more than 30 days ahead of today
    cutoff = (datetime.now() + timedelta(days=30)).date()
    try:
        dates = pd.to_datetime(df["trade_date"]).dt.date
        future = df[dates > cutoff]
        if len(future):
            report.add(ValidationIssue(
                "warning", "schema", "trade_date",
                f"{len(future)} trades with future dates (>{cutoff})",
                len(future),
                future["trade_id"].head(5).tolist(),
            ))
    except Exception:
        report.add(ValidationIssue("warning", "schema", "trade_date", "Could not parse trade_date column"))

    # Currency pair format
    bad_ccy = df[~df["currency"].str.contains("/", na=False)]
    if len(bad_ccy):
        report.add(ValidationIssue(
            "warning", "schema", "currency",
            f"{len(bad_ccy)} trades with invalid currency pair format (no /)",
            len(bad_ccy),
        ))

    # Mismatched base_ccy vs currency
    if "base_ccy" in df.columns and "currency" in df.columns:
        expected_base = df["currency"].str.split("/").str[0]
        mismatched = df[df["base_ccy"] != expected_base]
        if len(mismatched):
            report.add(ValidationIssue(
                "warning", "schema", "base_ccy",
                f"{len(mismatched)} trades where base_ccy doesn't match currency pair",
                len(mismatched),
                mismatched["trade_id"].head(5).tolist(),
            ))


def _validate_referential_integrity(df: pd.DataFrame, report: ValidationReport) -> None:
    """Check that matched_trade_id references exist and matching is 1-to-1."""

    all_trade_ids = set(df["trade_id"].unique())

    # Every matched_trade_id must reference a real trade
    matched_rows = df[df["matched_trade_id"].notna()]
    if len(matched_rows):
        orphaned = matched_rows[~matched_rows["matched_trade_id"].isin(all_trade_ids)]
        if len(orphaned):
            report.add(ValidationIssue(
                "error", "integrity", "matched_trade_id",
                f"{len(orphaned)} matched_trade_id references point to non-existent trades",
                len(orphaned),
                orphaned["trade_id"].head(5).tolist(),
            ))

    # Status=Matched must have a matched_trade_id
    matched_status = df[df["status"] == "Matched"]
    no_partner = matched_status[matched_status["matched_trade_id"].isna()]
    if len(no_partner):
        report.add(ValidationIssue(
            "error", "integrity", "matched_trade_id",
            f"{len(no_partner)} trades with status=Matched but no matched_trade_id",
            len(no_partner),
            no_partner["trade_id"].head(5).tolist(),
        ))

    # Status != Matched should NOT have a matched_trade_id (info only)
    non_matched = df[(df["status"] != "Matched") & (df["matched_trade_id"].notna())]
    if len(non_matched):
        report.add(ValidationIssue(
            "info", "integrity", "matched_trade_id",
            f"{len(non_matched)} non-Matched rows have a matched_trade_id set",
            len(non_matched),
        ))

    # 1-to-1 uniqueness: same (trade_a, trade_b) pairing should not appear twice
    if len(matched_rows):
        # Build order-independent pair keys without slow apply+frozenset
        mr = matched_rows[["trade_id", "matched_trade_id"]].copy()
        pair_a = mr[["trade_id", "matched_trade_id"]].min(axis=1)
        pair_b = mr[["trade_id", "matched_trade_id"]].max(axis=1)
        mr["_pair_key"] = pair_a + "|" + pair_b
        dup_pairs = mr[mr["_pair_key"].duplicated(keep=False)]
        dup_count = dup_pairs["_pair_key"].nunique()
        # Each pair appears twice = one match, so only flag if > 2
        real_dups = dup_pairs.groupby("_pair_key").size()
        actual_violations = (real_dups > 2).sum()
        if actual_violations:
            report.add(ValidationIssue(
                "error", "integrity", "matched_trade_id",
                f"{actual_violations} duplicate match pairings found (violates 1-to-1)",
                actual_violations,
            ))

    # Version 1 must exist for every trade
    v1_trades = set(df[df["version"] == 1]["trade_id"].unique())
    missing_v1 = all_trade_ids - v1_trades
    if missing_v1:
        report.add(ValidationIssue(
            "error", "integrity", "version",
            f"{len(missing_v1)} trades missing version 1",
            len(missing_v1),
            list(missing_v1)[:5],
        ))

    # Version must be contiguous per trade (no gaps)
    version_stats = df.groupby("trade_id")["version"].agg(["min", "max", "nunique", "count"])
    # Contiguous means: min=1, max=nunique (no gaps), nunique=count (no duplicates)
    # Duplicates from edge cases are ok, so just check min=1 and max=nunique
    non_contiguous = version_stats[
        (version_stats["min"] != 1)
        | (version_stats["max"] != version_stats["nunique"])
    ]
    if len(non_contiguous):
        sample = non_contiguous.index[:5].tolist()
        report.add(ValidationIssue(
            "warning", "integrity", "version",
            f"{len(non_contiguous)} trades have non-contiguous version sequences",
            len(non_contiguous),
            [str(s) for s in sample],
        ))


def _validate_temporal_consistency(df: pd.DataFrame, report: ValidationReport) -> None:
    """Check that last_action_tms is strictly increasing per trade across versions."""

    sorted_df = df.sort_values(["trade_id", "version"])
    sorted_df = sorted_df.copy()
    sorted_df["_ts"] = pd.to_datetime(sorted_df["last_action_tms"])
    sorted_df["_prev_ts"] = sorted_df.groupby("trade_id")["_ts"].shift(1)
    sorted_df["_same_trade"] = sorted_df["trade_id"] == sorted_df["trade_id"].shift(1)

    violations = sorted_df[
        sorted_df["_same_trade"]
        & sorted_df["_prev_ts"].notna()
        & (sorted_df["_ts"] <= sorted_df["_prev_ts"])
    ]

    violation_trades = violations["trade_id"].nunique()

    if violation_trades:
        sample_ids = violations["trade_id"].unique()[:5].tolist()
        report.add(ValidationIssue(
            "error", "temporal", "last_action_tms",
            f"{violation_trades} trades have non-increasing timestamps across versions",
            violation_trades,
            sample_ids,
        ))
    else:
        report.add(ValidationIssue(
            "info", "temporal", "last_action_tms",
            "All trades have strictly increasing timestamps across versions",
        ))


def _validate_lifecycle_transitions(df: pd.DataFrame, report: ValidationReport) -> None:
    """Check that status transitions follow the allowed transition map."""

    valid_transitions = {
        "Accepted": {"Unmatched", "Cancelled", "Rejected"},
        "Unmatched": {"Matched"},
        "Matched": {"Unmatched", "Settled"},
    }

    sorted_df = df.sort_values(["trade_id", "version"]).copy()
    sorted_df["_prev_status"] = sorted_df.groupby("trade_id")["status"].shift(1)
    sorted_df["_same_trade"] = sorted_df["trade_id"] == sorted_df["trade_id"].shift(1)

    # Only check rows that are transitions within the same trade
    transitions = sorted_df[sorted_df["_same_trade"] & sorted_df["_prev_status"].notna()].copy()

    # Skip consecutive identical statuses (duplicate submissions)
    transitions = transitions[transitions["status"] != transitions["_prev_status"]]

    if len(transitions) == 0:
        return

    # Build a set of valid (from, to) pairs
    valid_pairs = set()
    for from_s, to_set in valid_transitions.items():
        for to_s in to_set:
            valid_pairs.add((from_s, to_s))

    # Check which transitions are invalid
    transitions["_pair"] = list(zip(transitions["_prev_status"], transitions["status"]))
    # Only flag transitions where the from_status is in the transition map
    transitions["_from_known"] = transitions["_prev_status"].isin(valid_transitions.keys())
    transitions["_valid"] = transitions["_pair"].isin(valid_pairs)

    invalid = transitions[transitions["_from_known"] & ~transitions["_valid"]]
    violation_trades = invalid["trade_id"].nunique()

    if violation_trades:
        sample_ids = invalid["trade_id"].unique()[:5].tolist()
        report.add(ValidationIssue(
            "error", "integrity", "status",
            f"{violation_trades} trades have invalid status transitions",
            violation_trades,
            sample_ids,
        ))


def _validate_distribution_drift(
    df: pd.DataFrame,
    rules: dict[str, Any],
    drift_report: dict[str, Any] | None,
    report: ValidationReport,
    tolerance: float = 0.05,
) -> None:
    """Check that actual distributions match requested within tolerance."""

    if drift_report:
        report.stats["drift_report"] = drift_report

        for ccy, info in drift_report.get("currency_drift", {}).items():
            if info["drift"] > tolerance:
                report.add(ValidationIssue(
                    "warning", "distribution", "currency",
                    f"{ccy} drift {info['drift']:.2%} exceeds tolerance "
                    f"(target={info['target']:.1%}, actual={info['actual']:.1%})",
                ))

        for tt, info in drift_report.get("type_drift", {}).items():
            if info["drift"] > tolerance:
                report.add(ValidationIssue(
                    "warning", "distribution", "trade_type",
                    f"{tt} drift {info['drift']:.2%} exceeds tolerance "
                    f"(target={info['target']:.1%}, actual={info['actual']:.1%})",
                ))

    # Also compute from the DataFrame directly for cross-check
    v1_df = df[df["version"] == 1]
    total = len(v1_df)

    if total > 0:
        report.stats["actual_currency_distribution"] = (
            v1_df["currency"].value_counts(normalize=True).round(4).to_dict()
        )
        report.stats["actual_trade_type_distribution"] = (
            v1_df["trade_type"].value_counts(normalize=True).round(4).to_dict()
        )
        report.stats["actual_status_distribution"] = (
            df["status"].value_counts().to_dict()
        )
        report.stats["actual_direction_distribution"] = (
            v1_df["direction"].value_counts(normalize=True).round(4).to_dict()
        )


def _compute_stats(df: pd.DataFrame, report: ValidationReport) -> None:
    """Compute summary stats for the report."""

    unique_trades = df["trade_id"].nunique()
    total_rows = len(df)
    v1_df = df[df["version"] == 1]

    report.stats.update({
        "total_rows": total_rows,
        "unique_trades": unique_trades,
        "avg_versions_per_trade": round(total_rows / max(unique_trades, 1), 2),
        "unique_parties": df["party"].nunique(),
        "unique_counterparties": df["counterparty"].nunique(),
        "unique_currencies": df["currency"].nunique(),
        "notional_stats": {
            "min": round(df["notional"].min(), 2),
            "max": round(df["notional"].max(), 2),
            "mean": round(df["notional"].mean(), 2),
            "median": round(df["notional"].median(), 2),
        },
        "status_counts": df["status"].value_counts().to_dict(),
        "trade_type_counts": v1_df["trade_type"].value_counts().to_dict() if len(v1_df) else {},
        "matched_count": len(df[df["status"] == "Matched"]),
        "settled_count": len(df[df["status"] == "Settled"]),
        "terminal_trade_count": len(
            df.groupby("trade_id")["status"]
            .last()
            .isin({"Settled", "Cancelled", "Rejected"})
            .pipe(lambda s: s[s])
        ),
    })


def _detect_edge_cases(df: pd.DataFrame, report: ValidationReport) -> None:
    """Detect and report which edge cases are present in the dataset."""

    detected = []

    null_rates = df["spot_rate"].isnull().sum()
    if null_rates:
        detected.append(f"null_rates ({null_rates})")

    neg_notional = (df["notional"] < 0).sum()
    if neg_notional:
        detected.append(f"negative_notional ({neg_notional})")

    extreme = (df["notional"].abs() > 100_000_000).sum()
    if extreme:
        detected.append(f"extreme_values ({extreme})")

    try:
        cutoff = (datetime.now() + timedelta(days=30)).date()
        future = (pd.to_datetime(df["trade_date"]).dt.date > cutoff).sum()
        if future:
            detected.append(f"future_dates ({future})")
    except Exception:
        pass

    # Duplicate rows (same trade_id + version appearing more than once)
    dups = df.duplicated(subset=["trade_id", "version"], keep=False)
    dup_count = dups.sum()
    if dup_count:
        detected.append(f"duplicate_submissions ({dup_count})")

    # Mismatched base_ccy
    if "base_ccy" in df.columns:
        expected_base = df["currency"].str.split("/").str[0]
        mismatched = (df["base_ccy"] != expected_base).sum()
        if mismatched:
            detected.append(f"mismatched_ccy ({mismatched})")

    report.stats["detected_edge_cases"] = detected


# ── Public API ───────────────────────────────────────────────────────

def validate_dataset(
    df: pd.DataFrame,
    rules: dict[str, Any] | None = None,
    drift_report: dict[str, Any] | None = None,
    drift_tolerance: float = 0.05,
) -> ValidationReport:
    """
    Run all validation checks and return a structured report.

    Parameters
    ----------
    df : pd.DataFrame
        The generated trades dataset.
    rules : dict, optional
        The generation rules (for distribution drift checking).
    drift_report : dict, optional
        The DistributionTracker drift report (from generator).
    drift_tolerance : float
        Maximum acceptable drift from target distribution.

    Returns
    -------
    ValidationReport
        Structured report with issues, stats, and agent-readable summary.
    """
    report = ValidationReport()

    _validate_schema(df, report)
    _validate_referential_integrity(df, report)
    _validate_temporal_consistency(df, report)
    _validate_lifecycle_transitions(df, report)
    _validate_distribution_drift(df, rules or {}, drift_report, report, drift_tolerance)
    _compute_stats(df, report)
    _detect_edge_cases(df, report)

    if not any(i.severity == "error" for i in report.issues):
        report.add(ValidationIssue("info", "summary", None, "Dataset passed all validation checks"))

    return report
