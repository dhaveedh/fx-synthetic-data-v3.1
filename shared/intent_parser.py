"""
Intent Parser (v3)
===================

Converts natural language requests into validated GenerationConfig dicts.

v3 changes vs v2:
  - Uses Claude tool use (CONFIG_TOOL) to guarantee structured output. The model
    is forced to populate the tool schema, so no JSON extraction or markdown
    stripping is needed.
  - CONFIG_TOOL is the single source of truth for the config schema — the system
    prompt no longer embeds a duplicate JSON schema block.
  - parse_intent() validates the returned dict through GenerationConfig before
    returning, so invalid model output fails fast with a clear error.
  - parse_intent_with_feedback() accepts the previous_config dict and passes it
    to invoke_claude_with_feedback() for a genuine multi-turn conversation.
"""

from __future__ import annotations

from typing import Any

from shared.claude_client import invoke_claude, invoke_claude_with_feedback
from shared.models import GenerationConfig


SYSTEM_PROMPT = """\
You are an FX trade data scenario architect at a tier-1 investment bank.

Given a user's natural language request, call the generate_config tool with a
structured configuration for a synthetic FX trade generator.

Key rules:
- Currency distribution values MUST sum to 1.0 (±0.01)
- Trade type distribution values MUST sum to 1.0 (±0.01)
- Direction distribution MUST sum to 1.0
  - Market makers: close to 50/50
  - Hedge funds / directional desks: skew toward Buy or Sell (e.g. 60/40)
  - Stress test / sell-off: heavy Sell bias (e.g. {"Buy": 0.3, "Sell": 0.7})
- Valid currency pairs (market convention):
  G10 majors: EUR/USD, USD/JPY, GBP/USD, AUD/USD, USD/CAD, USD/CHF, NZD/USD
  G10 crosses: EUR/GBP, EUR/JPY, EUR/CHF, EUR/AUD, EUR/CAD, EUR/NZD, EUR/NOK, EUR/SEK,
               GBP/JPY, GBP/CHF, GBP/AUD, GBP/CAD, GBP/NZD,
               AUD/JPY, AUD/NZD, AUD/CAD, AUD/CHF,
               NZD/JPY, NZD/CAD, NZD/CHF, CAD/JPY, CAD/CHF, CHF/JPY
  Scandis: USD/NOK, USD/SEK, NOK/SEK
  EM/minor: USD/KRW, USD/HKD, USD/SGD, USD/ILS, USD/MXN, USD/CNH
- Only include pairs the user asks for; default to G10 majors if unspecified
- If the user asks for one currency pair only (e.g. "only USD/CNH trades"),
  set currency_config.distribution to {"<that pair>": 1.0} — do not spread
  weight across other pairs
- Include only the edge cases the scenario needs
- "stress test" or "anomalies" → include relevant edge cases
- "clean" or "audit" → empty edge_cases array
- Default seed=42 unless the user asks for randomness
- match_probability: how often trades find a matching partner
- settlement_ratio: how often matched trades proceed to settlement
- single_day: set true when the user asks for "a day's trading", "one trading
  day", "a full day's sample", etc. — this confines every trade_date (and the
  start of each trade's activity) to exactly reference_date, instead of the
  default behavior of scattering trade dates across the past ~30 days
- reference_date: the anchor date (ISO datetime, e.g. "2025-06-01T09:00:00").
  Set it when the user names a specific date; otherwise omit it and the
  generator defaults to today at 09:00
"""


# Single source of truth for the config schema.
# This definition is used both as the tool the model must call AND as the
# validation contract — GenerationConfig.from_dict() + validate() enforce it.
CONFIG_TOOL: dict[str, Any] = {
    "name": "generate_config",
    "description": "Return a structured FX trade generation configuration.",
    "input_schema": {
        "type": "object",
        "properties": {
            "volume": {
                "type": "integer",
                "description": "Number of trades to generate",
                "minimum": 1,
            },
            "seed": {
                "type": "integer",
                "description": "RNG seed for determinism. Default 42 unless randomness requested.",
            },
            "match_probability": {
                "type": "number",
                "description": "Probability (0-1) that a trade finds a matching partner",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "settlement_ratio": {
                "type": "number",
                "description": "Probability (0-1) that matched trades proceed to settlement",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "currency_config": {
                "type": "object",
                "description": "Currency pair distribution. Values must sum to ~1.0.",
                "properties": {
                    "distribution": {
                        "type": "object",
                        "additionalProperties": {"type": "number"},
                    }
                },
                "required": ["distribution"],
            },
            "trade_type_distribution": {
                "type": "object",
                "description": "Trade type weights (Spot/Forward/Swap). Must sum to ~1.0.",
                "additionalProperties": {"type": "number"},
            },
            "direction_distribution": {
                "type": "object",
                "description": "Direction weights (Buy/Sell). Must sum to 1.0.",
                "additionalProperties": {"type": "number"},
            },
            "edge_cases": {
                "type": "array",
                "description": "Edge cases to inject into the dataset",
                "items": {
                    "type": "string",
                    "enum": [
                        "null_rates",
                        "extreme_values",
                        "future_dates",
                        "negative_notional",
                        "duplicate_submissions",
                        "mismatched_ccy",
                    ],
                },
            },
            "edge_case_rates": {
                "type": "object",
                "description": "Override default injection rates per edge case (optional)",
                "additionalProperties": {"type": "number"},
            },
            "scenario_name": {
                "type": "string",
                "description": "Short snake_case identifier for the scenario",
            },
            "scenario_description": {
                "type": "string",
                "description": "One-sentence description of the scenario",
            },
            "reference_date": {
                "type": "string",
                "description": "ISO datetime anchor for generation, e.g. '2025-06-01T09:00:00'. "
                                "Omit to default to today at 09:00.",
            },
            "single_day": {
                "type": "boolean",
                "description": "When true, confine every trade to reference_date exactly instead of "
                                "scattering trade dates across the past ~30 days. Set true for requests "
                                "like 'a day's trading', 'one trading day', 'a full day's sample'.",
            },
        },
        "required": [
            "volume",
            "seed",
            "match_probability",
            "settlement_ratio",
            "currency_config",
            "trade_type_distribution",
            "direction_distribution",
            "edge_cases",
            "scenario_name",
            "scenario_description",
        ],
    },
}


def _validate(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Run the returned config dict through GenerationConfig validation.
    Raises ValueError if the model returned something structurally invalid.
    Returns the original dict on success (callers expect a plain dict).
    """
    errors = GenerationConfig.from_dict(raw).validate()
    if errors:
        raise ValueError(f"Agent returned invalid config: {'; '.join(errors)}")
    return raw


def parse_intent(user_input: str) -> dict[str, Any]:
    """
    Convert natural language to a validated generation config dict.

    Uses tool use to force structured output — the model must populate
    CONFIG_TOOL's schema, so no JSON parsing or markdown stripping is needed.
    The result is validated through GenerationConfig before being returned.
    """
    raw = invoke_claude(
        prompt=user_input,
        system=SYSTEM_PROMPT,
        tools=[CONFIG_TOOL],
        temperature=0.0,
    )
    return _validate(raw)


def parse_intent_with_feedback(
    user_input: str,
    validation_feedback: str,
    previous_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Re-parse with validation feedback from a previous generation run.

    When previous_config is provided, the model receives a three-turn conversation:
      1. The original user request
      2. Its previous config (as an assistant message)
      3. What went wrong and a request to correct it

    This gives the model full context for targeted corrections rather than
    regenerating blindly from the original prompt alone.
    """
    raw = invoke_claude_with_feedback(
        user_input=user_input,
        previous_config=previous_config or {},
        validation_feedback=validation_feedback,
        system=SYSTEM_PROMPT,
        tools=[CONFIG_TOOL],
    )
    return _validate(raw)
