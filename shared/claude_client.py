"""
Claude Client — AWS Bedrock (strict)
======================================

v3.1 runs strictly on AWS Bedrock — there is no auto-detection or fallback to
the Anthropic API, and no CLAUDE_BACKEND override to switch to it. Backend
selection is fixed; the only thing that can fail is Bedrock itself not being
configured, which raises ImportError at import time so callers can catch it
and disable NL features gracefully.

ANTHROPIC_API_KEY may still be set in the environment or .env file — it's
simply unused. It is not required, and setting it has no effect on which
backend is used.

Environment variables:
  BEDROCK_MODEL_ID  — Bedrock model (default: anthropic.claude-3-5-sonnet-20241022-v2:0)
  BEDROCK_REGION    — AWS region (default: eu-west-1)

Structured output: pass `tools` to invoke_claude()/invoke_claude_with_feedback()
— tool_choice forces a tool_use block in the response, guaranteeing a valid
dict with no text parsing required. Without tools, falls back to best-effort
JSON extraction from text.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


# ── Load .env file ──────────────────────────────────────────────────

def _load_dotenv() -> None:
    """Read .env file from project root and set any missing env vars."""
    current = Path(__file__).resolve().parent.parent
    env_file = current / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


# ── Backend detection ────────────────────────────────────────────────

def _detect_backend() -> str:
    """This version runs strictly on AWS Bedrock — verify it's reachable."""
    try:
        import boto3
        sts = boto3.client("sts")
        sts.get_caller_identity()
        return "bedrock"
    except Exception as e:
        raise ImportError(
            "AWS Bedrock is not available. Configure AWS credentials "
            "(e.g. `aws configure`) and ensure boto3 is installed.\n"
            f"Underlying error: {e}"
        ) from e


BACKEND = _detect_backend()


# ── Cached client ────────────────────────────────────────────────────

_bedrock_client = None


def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3
        region = os.getenv("BEDROCK_REGION", "eu-west-1")
        _bedrock_client = boto3.client("bedrock-runtime", region_name=region)
    return _bedrock_client


# ── Bedrock backend ─────────────────────────────────────────────────

def _invoke_bedrock(
    messages: list[dict],
    system: str | None = None,
    tools: list[dict] | None = None,
    max_tokens: int = 2000,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Call Claude via AWS Bedrock. Returns structured dict from tool input or text."""
    from botocore.exceptions import ClientError

    model_id = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0")
    client = _get_bedrock_client()

    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        body["system"] = system
    if tools:
        body["tools"] = tools
        body["tool_choice"] = {"type": "tool", "name": tools[0]["name"]}

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.invoke_model(
                modelId=model_id,
                body=json.dumps(body),
                contentType="application/json",
            )
            response_body = json.loads(response["body"].read())
            return _extract_response(response_body, used_tools=bool(tools))

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("ThrottlingException", "ServiceUnavailableException"):
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"  [bedrock] Retry {attempt + 1}/{MAX_RETRIES} after {delay}s ({error_code})")
                time.sleep(delay)
                last_error = e
                continue
            raise RuntimeError(f"Bedrock API error: {e}") from e

        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_DELAY)
                continue

    raise RuntimeError(f"Bedrock: all {MAX_RETRIES} retries exhausted. Last error: {last_error}")


# ── Response extraction ──────────────────────────────────────────────

def _extract_response(response: dict[str, Any], used_tools: bool) -> dict[str, Any]:
    """
    Extract structured output from a Bedrock response.

    When tools were used: pulls the tool_use input block — always a valid dict,
    no text parsing needed. Without tools: falls back to JSON extraction from text.
    """
    if used_tools:
        for block in response.get("content", []):
            if block.get("type") == "tool_use":
                return block.get("input", {})
        raise RuntimeError(
            f"Model did not return a tool_use block despite tool_choice being set "
            f"(stop_reason={response.get('stop_reason', 'unknown')!r})"
        )

    # Text-only fallback — only reached when no tools were passed to the call
    text = response["content"][0]["text"]
    return _extract_json_fallback(text)


def _extract_json_fallback(text: str) -> dict[str, Any]:
    """
    Best-effort JSON extraction from plain text.
    Only used when tool_use is not active (i.e., no tools were passed to the call).
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = text
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1]
    if "```" in cleaned:
        cleaned = cleaned.split("```", 1)[0]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start, end = text.find("{"), text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    raise RuntimeError(f"Could not extract JSON from response: {text[:200]}...")


# ── Unified interface ────────────────────────────────────────────────

def invoke_claude(
    prompt: str,
    system: str | None = None,
    tools: list[dict] | None = None,
    max_tokens: int = 2000,
    temperature: float = 0.0,
    **kwargs,
) -> dict[str, Any]:
    """
    Invoke Claude (Bedrock) and return a dict.

    Pass `tools` to use structured output — tool_choice forces a tool_use block
    in the response, guaranteeing a valid dict with no text parsing required.
    Without tools, falls back to best-effort JSON extraction from text.
    """
    messages = [{"role": "user", "content": prompt}]
    return _invoke_bedrock(messages, system, tools, max_tokens, temperature)


def invoke_claude_with_feedback(
    user_input: str,
    previous_config: dict[str, Any],
    validation_feedback: str,
    system: str | None = None,
    tools: list[dict] | None = None,
    max_tokens: int = 2000,
    **kwargs,
) -> dict[str, Any]:
    """
    Invoke Claude (Bedrock) as a multi-turn conversation for the feedback loop.

    The model sees three turns: the original request, its previous config as an
    assistant message, and then what went wrong — giving it the full context to
    make targeted corrections rather than regenerating from scratch.
    """
    messages = [
        {"role": "user", "content": user_input},
        {"role": "assistant", "content": json.dumps(previous_config, indent=2)},
        {
            "role": "user",
            "content": (
                f"The generated data failed validation:\n\n{validation_feedback}\n\n"
                "Please return a corrected configuration that addresses these issues."
            ),
        },
    ]
    return _invoke_bedrock(messages, system, tools, max_tokens)
