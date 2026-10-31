"""Gateway-owned xAI native X-search operation."""
import argparse

import json

import os

import re

import sys

from datetime import datetime, timedelta

from typing import Dict, List, Optional, Tuple

XAI_BASE_URL = "https://api.x.ai/v1"

def _xai_token() -> str:
    """SuperGrok subscription grant (subscriptions only, 2026-09-18).

    Resolved through the shared ladder: ~/.grok/auth.json only. A prepaid
    xAI key is never read.
    """
    from . import engine as model_ladder

    auth = model_ladder.subscription_auth("grok")
    return auth.token if auth is not None else ""

def xai_search(account: str, days: int = 3) -> Dict:
    """
    Search X/Twitter using xAI's Responses API with x_search tool.
    Uses curl subprocess for reliable HTTPS handling.
    """
    import subprocess
    from .engine import scoped_timeout

    budget = scoped_timeout(300)
    token = _xai_token()
    if not token:
        raise ValueError("Grok subscription is not available (no ~/.grok/auth.json grant)")

    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    # Build the request - mention account in prompt (faster than using filters)
    # Note: Using allowed_x_handles filter causes slow pagination; prompting is faster
    query = f"List all stock tickers mentioned by @{account} in the last {days} days. Format each as: $TICKER: Bullish/Bearish - brief reason"

    from research.humanizer import VOCABULARY, QUALITATIVE, WEIGHTS
    editorial = (
        "Humanizer: review every generated reason before returning it. Preserve facts, attribution "
        "and source uncertainty; never rewrite quoted source text or fabricate quotations. "
        "Start at 100, clamp 0-100, require at least 90. Deduct vocabulary per occurrence; "
        "other numeric categories per distinct passage. Apply both when applicable: "
        + json.dumps(WEIGHTS) + ". Vocabulary: " + ", ".join(VOCABULARY)
        + ". Contextual vocabulary: abstract landscape, underscore and highlight as verbs only. "
        "Remove remaining qualitative patterns without invented numerical weights: "
        + "; ".join(QUALITATIVE.values())
        + ". Prohibit negative parallelism including X, not Y and X. Not Y. "
        "Rewrite offending prose and rescore the entire result after each change. "
        "Keep this editorial self-assessment separate from the returned ticker data."
    )
    payload = {
        "model": "grok-4-0709",  # Required for x_search tool
        "input": [
            {"role": "system", "content": editorial},
            {
                "role": "user",
                "content": query
            }
        ],
        "tools": [
            {
                "type": "x_search"
                # Note: Omitting filters is faster - the model handles account filtering via the prompt
            }
        ],
        "store": False
    }

    # Use curl for reliable HTTPS - Python's SSL on this machine has issues
    # Note: xAI x_search can take 2-3 minutes for comprehensive account searches
    # The Authorization header goes in via a stdin config file, never argv:
    # argv is world-readable through ps for the whole 2-3 minute call.
    cmd = [
        "curl", "-s", "-X", "POST",
        f"{XAI_BASE_URL}/responses",
        "--config", "-",
        "-H", "Content-Type: application/json",
        "--max-time", f"{budget:g}",  # 5 minute timeout
        "-d", json.dumps(payload)
    ]
    curl_config = f'header = "Authorization: Bearer {token}"\n'

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=scoped_timeout(320),
                                input=curl_config)
        scoped_timeout(300)
        if result.returncode == 28:  # curl timeout
            raise RuntimeError("API request timed out (300s). The xAI API may be slow for this account.")
        if result.returncode != 0:
            raise RuntimeError(f"curl failed (code {result.returncode}): {result.stderr}")

        if not result.stdout.strip():
            raise RuntimeError("Empty response from API")

        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Request timed out (300s)")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON response: {e}\nResponse: {result.stdout[:500]}")
