"""
Bedrock Runner — Headless pipeline for AWS Lambda / Step Functions.

Accepts a JSON event with either:
  - "prompt": natural language request → agent parses → pipeline
  - "config": pre-built config dict → pipeline directly
"""

from __future__ import annotations

import json
from typing import Any

from orchestration.local_runner import run_pipeline, get_default_rules
from services.reference_data_service import ReferenceDataService


def handler(event: dict[str, Any]) -> dict[str, Any]:
    """
    Lambda-compatible entry point.

    Event schema:
    {
      "prompt": "Generate 5000 stress test trades...",  // OR
      "config": { ... },                                // direct config
      "max_iterations": 3,                              // optional
      "drift_tolerance": 0.05,                          // optional
      "filename": "trades.csv"                          // optional
    }
    """
    ref_service = ReferenceDataService()
    reference_data = ref_service.get_reference_data()

    user_input = event.get("prompt")
    rules = event.get("config")

    if not rules and user_input:
        try:
            from shared.intent_parser import parse_intent
            rules = parse_intent(user_input)
        except Exception as e:
            return {"status": "error", "message": f"Intent parsing failed: {e}"}

    if not rules:
        rules = get_default_rules()

    try:
        result = run_pipeline(
            rules=rules,
            reference_data=reference_data,
            max_iterations=event.get("max_iterations", 3),
            drift_tolerance=event.get("drift_tolerance", 0.05),
            user_input=user_input,
            filename=event.get("filename", "trades.csv"),
            verbose=False,
        )

        return {
            "status": "success",
            "scenario": result.config.scenario_name,
            "iterations": result.iteration,
            "total_rows": result.total_rows,
            "unique_trades": result.unique_trades,
            "validation_passed": result.validation_passed,
            "validation_summary": result.validation_summary,
            "output_file": result.trades_path,
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        event = json.loads(sys.argv[1])
    else:
        event = {"prompt": "Generate 1000 trades for a standard BAU day"}

    result = handler(event)
    print(json.dumps(result, indent=2))
