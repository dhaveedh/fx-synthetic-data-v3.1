"""
Bedrock Client — Backward compatibility wrapper.

All functionality has moved to claude_client.py which auto-detects
whether to use Bedrock or Anthropic API. This module re-exports
the same interface so existing imports continue to work.
"""

from shared.claude_client import invoke_claude, invoke_claude_with_feedback
