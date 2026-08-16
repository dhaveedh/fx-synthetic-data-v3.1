"""
Local Runner (v2) — Orchestrator with Agent Feedback Loop
==========================================================

Architecture:
  1. User prompt → Agent (Claude/Bedrock) → Config JSON
  2. Config → Sanitise → Generator (deterministic) → DataFrame
  3. DataFrame → Deep Validator → ValidationReport
  4. IF validation fails or drift exceeds tolerance:
       ValidationReport.summary → Agent → Adjusted Config → GOTO 2
  5. ELSE: Save to CSV → Done

Max iterations configurable (default: 3). Each iteration the agent
sees the validation feedback and adjusts. This closes the feedback
loop that was missing in v1.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from services.generate_fx_data.handler import handle_generate_and_save
from services.reference_data_service import ReferenceDataService
from services.validate_dataset.validator import validate_dataset
from shared.models import GenerationConfig, PipelineResult
from shared.utils import sanitise_config

# Optional NL support — auto-detects Bedrock or Anthropic API
NL_ENABLED = False
NL_BACKEND = None
NL_ERROR = None

try:
    from shared.claude_client import BACKEND as _backend
    from shared.intent_parser import parse_intent, parse_intent_with_feedback
    NL_ENABLED = True
    NL_BACKEND = _backend
except ImportError as e:
    NL_ERROR = str(e)


# ── Default configs ──────────────────────────────────────────────────

def get_default_rules() -> dict[str, Any]:
    return {
        "volume": 100_000,
        "seed": 42,
        "match_probability": 0.2,
        "settlement_ratio": 0.5,
        "currency_config": {
            "distribution": {
                "EUR/USD": 0.24,
                "USD/JPY": 0.13,
                "GBP/USD": 0.10,
                "AUD/USD": 0.06,
                "USD/CAD": 0.05,
                "USD/CHF": 0.04,
                "NZD/USD": 0.03,
                "EUR/GBP": 0.04,
                "EUR/JPY": 0.04,
                "GBP/JPY": 0.03,
                "AUD/JPY": 0.02,
                "EUR/CHF": 0.02,
                "USD/MXN": 0.03,
                "USD/SGD": 0.02,
                "USD/HKD": 0.02,
                "USD/KRW": 0.02,
                "USD/NOK": 0.02,
                "USD/SEK": 0.02,
                "EUR/NOK": 0.02,
                "EUR/SEK": 0.01,
                "USD/ILS": 0.01,
                "AUD/NZD": 0.01,
                "GBP/CHF": 0.01,
                "NZD/JPY": 0.01,
            }
        },
        "trade_type_distribution": {
            "Spot": 0.8,
            "Forward": 0.15,
            "Swap": 0.05,
        },
        "direction_distribution": {
            "Buy": 0.52,
            "Sell": 0.48,
        },
        "edge_cases": [],
        "scenario_name": "default_bau",
        "scenario_description": "Default business-as-usual generation",
    }


# ── Pipeline ─────────────────────────────────────────────────────────

def run_pipeline(
    rules: dict[str, Any],
    reference_data: dict[str, Any],
    max_iterations: int = 3,
    drift_tolerance: float = 0.05,
    user_input: str | None = None,
    filename: str = "trades.csv",
    verbose: bool = True,
    progress_cb=None,
    on_iteration_start=None,
) -> PipelineResult:
    """
    Run the full generation pipeline with feedback loop.

    Parameters
    ----------
    rules : dict
        Initial generation config (from agent or defaults).
    reference_data : dict
        Reference data from ReferenceDataService.
    max_iterations : int
        Max agent adjustment cycles before accepting results.
    drift_tolerance : float
        Acceptable distribution drift threshold.
    user_input : str, optional
        Original NL prompt (needed for feedback loop re-invocations).
    filename : str
        Output CSV filename.
    verbose : bool
        Print progress to stdout.

    Returns
    -------
    PipelineResult
    """
    def log(msg: str) -> None:
        if verbose:
            print(msg)

    for iteration in range(1, max_iterations + 1):
        if on_iteration_start:
            on_iteration_start(iteration, max_iterations)
        log(f"\n{'='*60}")
        log(f"  Iteration {iteration}/{max_iterations}")
        log(f"{'='*60}")

        # ── Step 1: Sanitise config ──
        try:
            rules = sanitise_config(rules)
        except ValueError as e:
            log(f"  [ERROR] Config validation failed: {e}")
            raise

        config = GenerationConfig.from_dict(rules)
        config_errors = config.validate()
        if config_errors:
            log(f"  [WARN] Config issues: {config_errors}")

        log(f"  Scenario: {config.scenario_name}")
        log(f"  Volume: {config.volume:,} trades")
        log(f"  Edge cases: {config.edge_cases or 'none'}")

        # ── Step 2: Generate ──
        log(f"  Generating trades...")
        t0 = time.time()
        df, tracker, path = handle_generate_and_save(
            rules=config.to_rules(),
            reference_data=reference_data,
            seed=config.seed,
            filename=filename,
            progress_cb=progress_cb,
        )
        elapsed = time.time() - t0
        log(f"  Generated {len(df):,} rows ({df['trade_id'].nunique():,} unique trades) in {elapsed:.1f}s")

        # ── Step 3: Validate ──
        log(f"  Running deep validation...")
        drift_report = tracker.get_drift_report()
        report = validate_dataset(
            df,
            rules=config.to_rules(),
            drift_report=drift_report,
            drift_tolerance=drift_tolerance,
        )

        log(f"  Validation: {'PASSED' if report.passed else 'FAILED'} "
            f"({report.error_count} errors, {report.warning_count} warnings)")

        for issue in report.issues:
            if issue.severity in ("error", "warning"):
                log(f"    [{issue.severity.upper()}] {issue.category}/{issue.field}: {issue.message}")

        if drift_report["max_drift"] > drift_tolerance:
            log(f"  [DRIFT] Max distribution drift: {drift_report['max_drift']:.2%} (tolerance: {drift_tolerance:.0%})")

        # ── Step 4: Decide — accept or iterate ──
        needs_retry = (
            not report.passed
            or drift_report["max_drift"] > drift_tolerance
        )

        if not needs_retry:
            log(f"\n  Dataset accepted. Saved to: {path}")
            return PipelineResult(
                config=config,
                trades_path=path,
                total_rows=len(df),
                unique_trades=df["trade_id"].nunique(),
                validation_passed=True,
                validation_summary=report.summary_for_agent(),
                drift_report=drift_report,
                iteration=iteration,
            )

        if iteration >= max_iterations:
            log(f"\n  Max iterations reached. Accepting current results with issues.")
            log(f"  Saved to: {path}")
            return PipelineResult(
                config=config,
                trades_path=path,
                total_rows=len(df),
                unique_trades=df["trade_id"].nunique(),
                validation_passed=report.passed,
                validation_summary=report.summary_for_agent(),
                drift_report=drift_report,
                iteration=iteration,
            )

        # ── Step 5: Feedback loop — ask agent to adjust ──
        if NL_ENABLED and user_input:
            log(f"\n  Feeding validation results back to agent for adjustment...")
            feedback = report.summary_for_agent()
            try:
                rules = parse_intent_with_feedback(user_input, feedback, previous_config=rules)
                log(f"  Agent returned adjusted config")
            except Exception as e:
                log(f"  [WARN] Agent feedback failed ({e}), keeping current config")
        else:
            log(f"  No agent available for feedback loop — accepting results")
            return PipelineResult(
                config=config,
                trades_path=path,
                total_rows=len(df),
                unique_trades=df["trade_id"].nunique(),
                validation_passed=report.passed,
                validation_summary=report.summary_for_agent(),
                drift_report=drift_report,
                iteration=iteration,
            )

    # Should not reach here, but safety return
    return PipelineResult(
        config=config,
        trades_path=path,
        total_rows=len(df),
        unique_trades=df["trade_id"].nunique(),
        validation_passed=False,
        validation_summary="Pipeline did not converge",
        drift_report={},
        iteration=max_iterations,
    )


# ── CLI entry point ──────────────────────────────────────────────────

def run() -> None:
    print("FX Synthetic Trade Generator v3")
    print("=" * 40)

    # Show backend status
    if NL_ENABLED:
        print(f"Agent backend: {NL_BACKEND}")
    else:
        print(f"Agent backend: not available")
        print(f"  Reason: {NL_ERROR}")
        print(f"  To enable: aws configure  (+ pip install boto3)")

    # Load reference data from CSVs
    ref_service = ReferenceDataService()
    reference_data = ref_service.get_reference_data()
    print(f"\nReference data loaded:")
    for k, v in ref_service.summary().items():
        print(f"  {k}: {v}")

    use_nl = input("\nUse natural language input? (y/n): ").strip().lower()

    user_input = None
    if use_nl == "y" and NL_ENABLED:
        user_input = input("Enter your request:\n> ")
        try:
            print(f"\nParsing intent via Claude ({NL_BACKEND})...")
            rules = parse_intent(user_input)
            print(f"Parsed config: {json.dumps(rules, indent=2)}")
        except Exception as e:
            print(f"Failed to parse input ({e}). Using defaults.")
            rules = get_default_rules()
    elif use_nl == "y" and not NL_ENABLED:
        print(f"\nAgent not available — {NL_ERROR}")
        print("Falling back to default rules.\n")
        rules = get_default_rules()
    else:
        print("Using default rules...")
        rules = get_default_rules()

    result = run_pipeline(
        rules=rules,
        reference_data=reference_data,
        user_input=user_input,
    )

    print(f"\n{'='*60}")
    print(f"  PIPELINE COMPLETE")
    print(f"{'='*60}")
    print(f"  Scenario:    {result.config.scenario_name}")
    print(f"  Iterations:  {result.iteration}")
    print(f"  Total rows:  {result.total_rows:,}")
    print(f"  Unique IDs:  {result.unique_trades:,}")
    print(f"  Validation:  {'PASSED' if result.validation_passed else 'FAILED'}")
    print(f"  Output:      {result.trades_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    run()
