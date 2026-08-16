# FX Synthetic Trade Generator v3.1

AI-driven synthetic FX trade data generation with a hybrid architecture: Claude (AWS Bedrock) for scenario reasoning, deterministic Python for generation, deep validation with an agent feedback loop.

---

## What's new in v3.1

v3.1 is built on top of v3 (see "What's new in v3" below for that layer). The changes in this version:

### 1. Schema-driven generation — no hardcoded field/column names

Every column in the generated `trades` table, and every column read from a reference CSV in `data/`, is now declared in a JSON schema file instead of being a Python literal.

- **`data/schema/trades.schema.json`** declares all 23 output columns. Each field has a `kind` (`lifecycle`, `weighted_choice`, `reference_choice`, `numeric_range`, `bernoulli`, `derived`) plus whatever that kind needs (a distribution path, a min/max range, a named function, which edge cases can mutate it). `generator.py`'s `COLUMNS` list, and `validator.py`'s `REQUIRED_COLUMNS`, are both *derived from this file* — they're no longer separately hardcoded and can't drift out of sync with each other.
- **`data/schema/*.schema.json`** (one per reference file — `parties`, `counterparties`, `currencies`, `venues`, `trade_types`, `trade_lifecycle`, `trade_status_transitions`) map **role → actual column name** for each CSV, so `reference_data_service.py` never hardcodes a literal column name like `party_id` or `buy_bias` — it asks the schema instead.
- Field dependency order and edge-case wiring are **inferred, not hand-written**: `schema_loader.py` statically scans each derived field's function source for the row values it actually reads, and a small convention table resolves `edge_cases: [...]` lists to their mutator functions — so the schema file only states *what* a field needs, not *how the engine gets there*.
- New engine modules: `services/generate_fx_data/schema_loader.py` (schema parsing, dependency inference, topological sort), `field_engine.py` (builds one trade's attributes by walking the schema), `field_functions.py` (the named derived/mutator functions, ported 1:1 from the old inline logic), `rng_utils.py`, `edge_cases.py`. `services/input_schema.py` does the equivalent for reference-data column roles.
- **Adding a new output field** is a JSON edit (plus one small registered Python function only if it needs real derivation logic — picking from a distribution, a numeric range, or a reference list needs zero Python).
- `generate_fx_data()` accepts an optional `schema_path` to point the generator at an entirely different table definition.

### 2. Strictly AWS Bedrock

`shared/claude_client.py` no longer auto-detects or falls back to the Anthropic API, and `CLAUDE_BACKEND` no longer has any effect — this version always runs on Bedrock. `ANTHROPIC_API_KEY` can still sit in `.env` harmlessly; it's simply unused. If Bedrock isn't reachable (no AWS credentials), NL features disable gracefully at startup rather than silently falling back to a different backend. The Anthropic API code path (SDK client, invocation, dependency) was removed entirely rather than left in as dead code.

### 3. Security fixes (path traversal)

A security review found two path-traversal issues, both fixed:
- `GET /api/download/{filename}` didn't validate the filename before joining it with the data directory — a crafted filename (drive-absolute path or `..` traversal) could read arbitrary files off the server. Now the filename is collapsed to a bare basename, checked against a strict allow-list pattern, and the resolved path is confirmed to stay inside the data directory before serving.
- The output filename was built from an unsanitized `scenario_name` (client-controlled via `/api/generate/config`, or agent-controlled via the NL prompt), so a crafted scenario name could make the generator write outside `data/`. Fixed at both ends: the filename is sanitized to safe characters before it's built, and `save_trades_to_csv`/`save_trades_chunked` independently refuse to write anywhere outside `DATA_DIR` regardless of how the filename got there.

### 4. New generation controls

- **`single_day`** (boolean rule/config field) — when set, every trade's `trade_date` and the start of its activity timestamps are confined to `reference_date` exactly, instead of the default behavior of scattering trade dates across the past ~30 days. Use this for "a full day's trading sample" style requests.
- **`reference_date`** is now exposed to the NL agent's tool schema (it existed in `GenerationConfig` before but the agent had no way to set it).
- **`USD/CNH`** added to the available currency pairs — `CNH` was already in `data/currencies.csv` but missing from the hardcoded conventional-pairs list, so it was silently unreachable even though the currency existed.

### 5. Real test suite

`tests/` now has 33 tests across `test_generator.py`, `test_config.py`, `test_schema_loader.py` (dependency ordering, cycle/validation errors), and `test_field_engine.py` (kind dispatch, edge-case mutators, a regression guard against the real `trades.schema.json`). The equivalent v3 `tests/` directory was empty.

### 6. Python 3.13

Rebuilt on Python 3.13 (this machine only had 3.14 installed) with matching numpy/pandas wheels.

---

## What's new in v3 (carried forward)

### Agent feedback loop
The pipeline iterates: if validation fails or distribution drift exceeds tolerance, the validation report is fed back to the agent, which adjusts the config and re-generates. Max 3 iterations by default, using a genuine multi-turn conversation (original request → previous config → what went wrong) rather than a single re-concatenated prompt.

```
User prompt → Agent → Config → Generator → Validator
                ↑                              │
                └──── feedback (if issues) ────┘
```

### Deep validation layer
- **Schema validation** — column types, value ranges, enum membership
- **Referential integrity** — every `matched_trade_id` references a real trade, matched trades have partners, version sequences are contiguous
- **Temporal consistency** — `last_action_tms` strictly increasing per trade across versions
- **Lifecycle transitions** — status changes follow the allowed transition map
- **Distribution drift** — actual vs requested distributions within configurable tolerance
- **Edge case detection** — reports which anomalies are present in the dataset

### Organic edge case injection
Edge cases are woven into the generation loop rather than bolted on after — a trade with a null rate or future date participates in the full lifecycle engine, producing realistic trails. The old `inject_edge_cases` module is deprecated.

Supported edge cases: `null_rates`, `extreme_values`, `future_dates`, `negative_notional`, `duplicate_submissions`, `mismatched_ccy`.

### Structured LLM output via tool use
The agent is forced to call a tool with a JSON-schema-validated config (`CONFIG_TOOL` in `shared/intent_parser.py`) instead of returning free text that needs markdown-fence stripping and best-effort JSON extraction. Responses are additionally validated through `GenerationConfig` before being trusted.

### SSE progress streaming
`POST /api/generate/stream` streams generation progress (parsing, per-trade progress, validating, complete) as server-sent events for the frontend's progress bar.

### Volume scaling
Chunked CSV writer for memory-efficient output at 100k+ trade volumes, automatically selected above 50k rows.

---

## Project structure

```
fx-synthetic-data-v3.1/
├── orchestration/
│   ├── local_runner.py             # CLI entry point with feedback loop
│   └── bedrock_runner.py           # Headless Lambda/Step Functions entry point
├── services/
│   ├── generate_fx_data/
│   │   ├── generator.py            # Main generation loop + lifecycle/matching engines
│   │   ├── schema_loader.py        # Loads trades.schema.json, infers dependency order
│   │   ├── field_engine.py         # Builds one trade's attributes from the schema
│   │   ├── field_functions.py      # Named derived-field / edge-case-mutator functions
│   │   ├── rng_utils.py            # Seeded RNG wrapper, weighted-choice helper
│   │   ├── edge_cases.py           # EdgeCaseConfig (per-case injection rates)
│   │   └── handler.py              # Service wrapper
│   ├── validate_dataset/
│   │   └── validator.py            # Deep validation (schema, integrity, temporal, drift)
│   ├── inject_edge_cases/
│   │   └── injector.py             # DEPRECATED — retained for backward compat
│   ├── input_schema.py             # Loads data/schema/*.schema.json (role → column)
│   └── reference_data_service.py   # Loads data/*.csv via input_schema
├── shared/
│   ├── claude_client.py            # AWS Bedrock client (strict — see "What's new")
│   ├── intent_parser.py            # NL → config via tool use, with feedback loop
│   ├── models.py                   # GenerationConfig, PipelineResult, shared enums
│   └── utils.py                    # Sanitisation, normalisation
├── frontend/
│   ├── package.json                # React + Vite
│   ├── vite.config.js              # Dev server with API proxy
│   ├── index.html
│   └── src/
│       ├── main.jsx                # Entry point
│       ├── App.jsx                 # Main UI component
│       ├── api.js                  # FastAPI client
│       └── index.css               # Design system tokens
├── api/
│   └── main.py                     # FastAPI: generate/stream/config/download/health
├── prompts/
│   ├── intent_parser.txt           # Agent system prompt (reference copy)
│   └── scenario_builder.txt        # Feedback loop adjustment prompt
├── agent/
│   ├── agent.md                    # Agent role description
│   ├── hooks.yaml                  # Pipeline hook definitions
│   └── skills.yaml                 # Agent skill registry
├── data/
│   ├── currencies.csv / parties.csv / counterparties.csv / venues.csv /
│   │   trade_types.csv / trade_lifecycle.csv / trade_status_transitions.csv
│   ├── schema/                     # One *.schema.json per file above, + trades.schema.json
│   └── trades.csv                  # Generated output (also trades_<scenario_name>.csv)
├── tests/                          # 33 tests: generator, config, schema_loader, field_engine
├── start.bat / start-backend.bat / start-frontend.bat / start.sh
├── requirements.txt
└── Readme.md
```

---

## Quick start

### One-command setup (Windows)

```bash
start.bat
```

On first run this creates a `.env` template (AWS Bedrock config) and opens it for editing. Fill in your AWS profile / region / model, then re-run `start.bat` to launch both services.

### One-command setup (Mac/Linux)

```bash
chmod +x start.sh && ./start.sh
```

Opens the UI at `http://localhost:3000` with the API at `http://127.0.0.1:8000`.

### Manual setup

**Backend** (Python 3.13):

```bash
python -m venv venv
venv\Scripts\activate                 # Windows
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
```

AWS credentials for Bedrock need to be available however you normally provide them (`aws configure`, `AWS_PROFILE`, instance role, etc.) — see Environment variables below.

**Frontend** (separate terminal):

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000` in your browser.

**CLI mode (no frontend needed):**

```bash
python -m orchestration.local_runner
```

**Headless / Lambda:**

```bash
python -m orchestration.bedrock_runner '{"prompt": "Generate 5000 stress test trades"}'
```

**Tests:**

```bash
pytest tests/ -q
```

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/generate` | NL prompt → agent → pipeline → full results |
| POST | `/api/generate/stream` | Same as above, streamed as SSE progress events |
| POST | `/api/generate/config` | Direct config → pipeline → full results (bypasses the agent) |
| GET | `/api/download/{filename}` | Download a generated CSV |
| GET | `/api/reference-data` | Inspect loaded reference data |
| GET | `/api/health` | Health check + backend status |
| GET | `/docs` | Swagger UI (auto-generated) |

---

## Configuration schema

```json
{
  "volume": 10000,
  "seed": 42,
  "match_probability": 0.3,
  "settlement_ratio": 0.5,
  "reference_date": "2026-08-16T09:00:00",
  "single_day": true,
  "currency_config": {
    "distribution": {
      "USD/CNH": 1.0
    }
  },
  "trade_type_distribution": {
    "Spot": 0.7,
    "Forward": 0.2,
    "Swap": 0.1
  },
  "direction_distribution": {
    "Buy": 0.5,
    "Sell": 0.5
  },
  "edge_cases": ["null_rates", "extreme_values"],
  "edge_case_rates": {
    "null_rates": 0.02
  },
  "scenario_name": "usdcnh_full_day",
  "scenario_description": "A full trading day of USD/CNH trades only"
}
```

`reference_date` defaults to today at 09:00 if omitted. `single_day` defaults to `false` (the v3 behavior — trade dates scattered across the past ~30 days).

---

## The schema files (`data/schema/`)

Two kinds of schema live here:

- **Reference-data schemas** (`parties.schema.json`, `counterparties.schema.json`, etc.) — a flat `{"role": "column_name"}` map per CSV, e.g. `{"id": "party_id", "status_filter": "status", "weight": "buy_bias"}`, plus an `active_value` for the status filter. `reference_data_service.py` reads columns through these instead of hardcoding names.
- **`trades.schema.json`** — the output table. Each entry is `{"name", "kind", ...kind-specific config}`. See the field-`kind` table under "What's new in v3.1" above. To add a column: append an entry; if it's a plain distribution/range/reference-list pick, that's the whole change. If it needs real derivation logic, also register a small function in `field_functions.py` and reference it by name.

---

## Trade lifecycle

Driven by `data/trade_status_transitions.csv`:

```
Accepted → Unmatched → Matched → Settled
         → Cancelled            → Unmatched (re-match cycle)
         → Rejected
```

Matching is strict 1-to-1. A trade can match with different partners across versions but the same pairing is never reused.

---

## Environment variables

This version runs strictly on AWS Bedrock — there's no backend auto-detection or override.

| Variable | Default | Description |
|----------|---------|--------------|
| `BEDROCK_MODEL_ID` | `anthropic.claude-3-5-sonnet-20241022-v2:0` | Bedrock model |
| `BEDROCK_REGION` | `eu-west-1` | AWS region |

AWS credentials themselves are picked up the normal boto3 way (`AWS_PROFILE`, `aws configure`, environment credentials, instance role, etc.) — not read from `.env`.

`ANTHROPIC_API_KEY` may still be present in `.env`; it has no effect and is not required.

If Bedrock isn't reachable, NL-driven generation (`/api/generate`, `/api/generate/stream`, the CLI's NL prompt) disables itself gracefully — direct-config generation (`/api/generate/config`, or passing a `rules` dict straight to `run_pipeline`) always works regardless.

---

## License

MIT
