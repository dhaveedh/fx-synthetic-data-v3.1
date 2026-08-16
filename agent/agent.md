# FX Synthetic Data Agent

## Role
Scenario reasoning and parameter tuning for synthetic FX trade generation.

## Capabilities
- Parse natural language requests into structured generation configs
- Reason about FX trade scenarios (stress tests, audits, BAU, reconciliation)
- Adjust configs based on validation feedback (feedback loop)
- Select appropriate edge cases and injection rates

## Feedback Loop
When validation fails or distribution drift exceeds tolerance, the agent
receives a structured summary and produces an adjusted config. Max 3 iterations.
