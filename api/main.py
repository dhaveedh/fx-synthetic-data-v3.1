"""
FastAPI backend for the FX Synthetic Trade Generator.

Endpoints:
  POST /api/generate         — NL prompt → agent → pipeline → full results
  POST /api/generate/config  — Direct config → pipeline → full results
  GET  /api/reference-data   — Reference data summary
  GET  /api/download/{file}  — Download generated CSV
  GET  /api/health           — Health check
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from orchestration.local_runner import run_pipeline, get_default_rules
from services.reference_data_service import ReferenceDataService
from services.validate_dataset.validator import validate_dataset

try:
    from shared.claude_client import BACKEND as CLAUDE_BACKEND
    from shared.intent_parser import parse_intent
    NL_ENABLED = True
except ImportError as e:
    NL_ENABLED = False
    CLAUDE_BACKEND = None

app = FastAPI(
    title="FX Synthetic Trade Generator",
    version="3.0.0",
    description="AI-driven synthetic FX trade data generation with feedback loop",
)

# CORS — allow React dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ref_service = ReferenceDataService()
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data"))

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")
_SAFE_CSV_FILENAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.csv$")


def _trades_filename(scenario_name: str | None) -> str:
    """
    Build a safe output filename from a (possibly attacker-controlled)
    scenario_name — strips anything but [A-Za-z0-9_-], so path separators,
    drive letters, and '..' can never reach the filesystem layer.
    """
    safe = _SAFE_NAME_RE.sub("", scenario_name or "")[:100]
    return f"trades_{safe or 'output'}.csv"


def _resolve_in_data_dir(filename: str) -> str:
    """
    Resolve `filename` (a bare basename, already validated by the caller)
    against DATA_DIR and confirm the result is actually inside DATA_DIR.
    Raises HTTPException(400) if it isn't.
    """
    candidate = os.path.realpath(os.path.join(DATA_DIR, filename))
    base = os.path.realpath(DATA_DIR)
    try:
        if os.path.commonpath([base, candidate]) != base:
            raise HTTPException(status_code=400, detail="Invalid filename")
    except ValueError:
        # commonpath raises if paths are on different drives — also invalid.
        raise HTTPException(status_code=400, detail="Invalid filename")
    return candidate


# ── Request models ───────────────────────────────────────────────────

class PromptRequest(BaseModel):
    prompt: str = Field(..., description="Natural language scenario description")
    max_iterations: int = Field(3, ge=1, le=5)
    drift_tolerance: float = Field(0.05, ge=0.01, le=0.2)
    max_rows: int = Field(5000, ge=100)


class ConfigRequest(BaseModel):
    config: dict[str, Any] = Field(..., description="Direct generation config")
    max_iterations: int = Field(3, ge=1, le=5)
    drift_tolerance: float = Field(0.05, ge=0.01, le=0.2)
    max_rows: int = Field(5000, ge=100)


# ── Response models ──────────────────────────────────────────────────

class ValidationIssueResponse(BaseModel):
    severity: str
    category: str
    field: str | None
    message: str
    count: int


class GenerateResponse(BaseModel):
    status: str
    scenario_name: str
    scenario_description: str
    iterations: int
    total_rows: int
    unique_trades: int
    validation_passed: bool
    validation_summary: str
    validation_issues: list[ValidationIssueResponse]
    stats: dict[str, Any]
    config: dict[str, Any]
    sample_trades: list[dict[str, Any]]
    download_filename: str


# ── Shared pipeline runner ───────────────────────────────────────────

def _run_and_format(rules: dict, user_input: str | None, req_max_iterations: int, req_drift_tolerance: float, req_max_rows: int) -> GenerateResponse:
    """Run pipeline and format response for the frontend."""
    reference_data = ref_service.get_reference_data()

    filename = _trades_filename(rules.get("scenario_name"))

    result = run_pipeline(
        rules=rules,
        reference_data=reference_data,
        max_iterations=req_max_iterations,
        drift_tolerance=req_drift_tolerance,
        user_input=user_input,
        filename=filename,
        verbose=False,
    )

    # Re-read generated data for validation details and sample
    import pandas as pd
    trades_path = result.trades_path
    df = pd.read_csv(trades_path)

    report = validate_dataset(
        df,
        rules=result.config.to_rules(),
        drift_report=result.drift_report,
        drift_tolerance=req_drift_tolerance,
    )

    # Build response
    issues = [
        ValidationIssueResponse(
            severity=i.severity,
            category=i.category,
            field=i.field,
            message=i.message,
            count=i.count,
        )
        for i in report.issues
    ]

    # Sample trades (first 200 rows)
    sample = df.head(200).fillna("").to_dict("records")

    return GenerateResponse(
        status="success",
        scenario_name=result.config.scenario_name,
        scenario_description=result.config.scenario_description,
        iterations=result.iteration,
        total_rows=result.total_rows,
        unique_trades=result.unique_trades,
        validation_passed=result.validation_passed,
        validation_summary=result.validation_summary,
        validation_issues=issues,
        stats=report.stats,
        config=result.config.to_rules(),
        sample_trades=sample,
        download_filename=filename,
    )


# ── Endpoints ────────────────────────────────────────────────────────

@app.post("/api/generate", response_model=GenerateResponse)
def generate_from_prompt(req: PromptRequest):
    """Generate trades from a natural language prompt via the agent."""
    if not NL_ENABLED:
        raise HTTPException(status_code=503, detail="NL parsing not available. Configure AWS Bedrock credentials.")

    try:
        rules = parse_intent(req.prompt)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Intent parsing failed: {e}")

    return _run_and_format(rules, req.prompt, req.max_iterations, req.drift_tolerance, req.max_rows)


@app.post("/api/generate/stream")
async def generate_from_prompt_stream(req: PromptRequest):
    """Generate trades with SSE progress streaming."""
    if not NL_ENABLED:
        raise HTTPException(status_code=503, detail="NL parsing not available. Configure AWS Bedrock credentials.")

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker() -> None:
        try:
            emit({"type": "phase", "pct": 5, "label": "Parsing scenario..."})
            try:
                rules = parse_intent(req.prompt)
            except Exception as e:
                emit({"type": "error", "message": f"Intent parsing failed: {e}"})
                return

            current_iter = [1]

            def on_iteration_start(iteration: int, max_iterations: int) -> None:
                current_iter[0] = iteration
                if iteration > 1:
                    emit({"type": "phase", "pct": 10,
                          "label": f"Adjusting config (iteration {iteration}/{max_iterations})..."})

            def on_progress(done: int, total: int) -> None:
                pct = 10 + int((done / total) * 70)
                iter_note = f" · iter {current_iter[0]}/{req.max_iterations}" if req.max_iterations > 1 else ""
                emit({"type": "progress", "pct": pct,
                      "label": f"Generating trades ({done:,} / {total:,}){iter_note}..."})

            reference_data = ref_service.get_reference_data()
            filename = _trades_filename(rules.get("scenario_name"))

            result = run_pipeline(
                rules=rules,
                reference_data=reference_data,
                max_iterations=req.max_iterations,
                drift_tolerance=req.drift_tolerance,
                user_input=req.prompt,
                filename=filename,
                verbose=False,
                progress_cb=on_progress,
                on_iteration_start=on_iteration_start,
            )

            emit({"type": "phase", "pct": 85, "label": "Validating..."})

            import pandas as pd
            df = pd.read_csv(result.trades_path)
            report = validate_dataset(
                df,
                rules=result.config.to_rules(),
                drift_report=result.drift_report,
                drift_tolerance=req.drift_tolerance,
            )

            issues = [
                {"severity": i.severity, "category": i.category,
                 "field": i.field, "message": i.message, "count": i.count}
                for i in report.issues
            ]
            sample = df.head(200).fillna("").to_dict("records")

            result_payload = {
                "status": "success",
                "scenario_name": result.config.scenario_name,
                "scenario_description": result.config.scenario_description,
                "iterations": result.iteration,
                "total_rows": result.total_rows,
                "unique_trades": result.unique_trades,
                "validation_passed": result.validation_passed,
                "validation_summary": result.validation_summary,
                "validation_issues": issues,
                "stats": report.stats,
                "config": result.config.to_rules(),
                "sample_trades": sample,
                "download_filename": filename,
            }
            emit({"type": "complete", "pct": 100, "result": result_payload})

        except Exception as e:
            emit({"type": "error", "message": str(e)})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=worker, daemon=True).start()

    async def event_stream():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/generate/config", response_model=GenerateResponse)
def generate_from_config(req: ConfigRequest):
    """Generate trades from a direct config (bypasses agent)."""
    return _run_and_format(req.config, None, req.max_iterations, req.drift_tolerance, req.max_rows)


@app.get("/api/download/{filename}")
def download_csv(filename: str):
    """Download a generated CSV file."""
    # Collapse to a bare basename first (neutralizes '..', drive letters, and
    # separators of either kind before any further check even runs), then
    # enforce a strict allowlist pattern, then re-confirm containment.
    filename = os.path.basename(filename)
    if not _SAFE_CSV_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = _resolve_in_data_dir(filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    return FileResponse(path, media_type="text/csv", filename=filename)


@app.get("/api/reference-data")
def get_reference_data():
    """Reference data summary."""
    return ref_service.summary()


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "version": "3.0.0",
        "nl_enabled": NL_ENABLED,
        "claude_backend": CLAUDE_BACKEND,
        "reference_data": ref_service.summary(),
    }
