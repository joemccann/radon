#!/usr/bin/env python3
"""Fetch MenthorQ dashboard chart data via S3 image download + Vision extraction.

Usage:
    python3 scripts/fetch_menthorq_dashboard.py --command vol
    python3 scripts/fetch_menthorq_dashboard.py --command eod [--date 2026-03-06]
    python3 scripts/fetch_menthorq_dashboard.py --command futures

Caches to: data/menthorq_cache/{command}_{DATE}.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clients.menthorq_client import (
    MenthorQClient,
    MenthorQError,
    MenthorQExtractionError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "menthorq_cache"

# Vision extraction prompts per dashboard type

EXTRACTION_PROMPTS: dict[str, str] = {
    "vol": """Extract volatility model data from this chart image.
Return ONLY a JSON object with this exact structure:
{
  "title": "Chart title as shown",
  "data": [
    {"metric": "Vol Control", "value": 0.85, "signal": "risk-on"},
    {"metric": "Vol Barometer", "value": 0.42, "signal": "neutral"}
  ],
  "metadata": {}
}

Rules:
- Extract all visible metrics, values, and signal states
- Values are decimal numbers as shown
- Signal states should be extracted as shown (e.g. "risk-on", "risk-off", "neutral")
- Return ONLY the JSON object, no markdown, no explanation""",

    "eod": """Extract end-of-day options data from this chart image.
Return ONLY a JSON object with this exact structure:
{
  "title": "Chart title as shown",
  "data": [...],
  "metadata": {}
}

Rules:
- Extract ALL visible data points, labels, and values
- Strike prices are integers
- GEX/DEX values in raw dollars (convert B/M/K abbreviations)
- Dates in YYYY-MM-DD format where applicable
- Return ONLY the JSON object, no markdown, no explanation""",
}

# Default prompt for any command not in EXTRACTION_PROMPTS
DEFAULT_PROMPT = """Extract all data from this dashboard chart image.
Return ONLY a JSON object with this structure:
{
  "title": "Chart title as shown",
  "data": [...],
  "metadata": {...}
}

Rules:
- Extract ALL visible data points, labels, and values
- Convert abbreviations: 1B = 1000000000, 1M = 1000000, 1K = 1000
- Dates in YYYY-MM-DD format where applicable
- Return ONLY the JSON object, no markdown, no explanation"""


def fetch_dashboard(
    command: str,
    date: str | None = None,
    *,
    headless: bool = True,
) -> dict:
    """Fetch a MenthorQ dashboard via screenshot + Vision.

    Args:
        command: Dashboard command slug (gex, dix, vix, etc.)
        date: Optional date YYYY-MM-DD. Defaults to today.
        headless: Run browser headless (default True).

    Returns:
        Extracted data dict with date, fetched_at, source, command, and data fields.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prompt = EXTRACTION_PROMPTS.get(command, DEFAULT_PROMPT)

    logger.info(f"Fetching MenthorQ dashboard: command={command}, date={date}")

    with MenthorQClient(headless=headless) as client:
        # Screenshot the dashboard
        png_bytes = client.get_dashboard_image(command)
        logger.info(f"Screenshot captured: {len(png_bytes):,} bytes")

        # Extract via Vision
        extracted = client._extract_via_vision(png_bytes, prompt)
        if not extracted:
            raise MenthorQExtractionError(
                f"Vision extraction returned no data for {command} dashboard."
            )

    # _extract_via_vision returns a list, but dashboard prompts return objects
    # Handle both cases
    if isinstance(extracted, list) and len(extracted) == 1:
        result_data = extracted[0]
    elif isinstance(extracted, list):
        result_data = {"data": extracted}
    else:
        result_data = extracted

    result = {
        "date": date,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "menthorq_s3_vision",
        "command": command,
        **result_data,
    }

    # Cache to file
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{command}_{date}.json"
    cache_file.write_text(json.dumps(result, indent=2))
    logger.info(f"Cached to: {cache_file}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Fetch MenthorQ dashboard chart data"
    )
    parser.add_argument(
        "--command",
        required=True,
        choices=["vol", "forex", "eod", "intraday", "futures", "cryptos_technical", "cryptos_options"],
        help="Dashboard command: vol, forex, eod, intraday, futures, cryptos_technical, cryptos_options",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show browser window",
    )
    args = parser.parse_args()

    try:
        result = fetch_dashboard(
            args.command,
            args.date,
            headless=not args.no_headless,
        )
        print(json.dumps(result, indent=2))
    except MenthorQError as exc:
        logger.error(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
