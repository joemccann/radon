#!/usr/bin/env python3.13
"""Radon LLM Token Expenditure Index — daily compute-cost macro signal.

WHY:
    The cost of frontier LLM inference is itself a market structure
    signal — it tracks GPU supply, provider pricing pressure, and the
    competitive landscape. Surfaced on the Regime tab alongside CRI/VCG
    as a slow-moving macro context (one row per UTC day).

WHAT:
    Pulls https://artificialanalysis.ai/api/v2/data/llms/models — the
    free Artificial Analysis API (1000 req/day quota). For a curated
    basket of frontier models (Claude Opus/Sonnet, GPT-4o, Gemini 2.5
    Pro, DeepSeek V3, Llama 3.1 405B, Mistral Large), computes:

        per-model blended USD per Mtok
          = 0.7 * price_1m_input_tokens + 0.3 * price_1m_output_tokens

        raw_avg_usd = median(blended across the basket)

    The 70/30 input/output split reflects typical agentic API workloads
    where read-heavy prompts dominate. The MEDIAN (not mean) makes the
    index robust to one outlier model (GPT-5 launches at $100/Mtok,
    DeepSeek launches at $0.10/Mtok, the basket median barely moves).

    On first persist, index_value = 1.0 (base). Subsequent days are
    `raw_today / raw_base`, matching Silicon Data's compute-cost index
    treatment (1.0 in Dec → 2.0 in May if inference doubles in price).

WHEN:
    Once per day at 06:30 UTC via systemd timer (radon-llm-index.timer
    on Hetzner). The script is idempotent on (date) — re-running on the
    same UTC day overwrites the row, never appends.

EXITS:
    0  — success (row persisted)
    1  — API down, malformed, or basket entirely missing from response

GRACEFUL DEGRADATION:
    - Missing individual models → logged + skipped (basket continues)
    - Zero models matched → exit 1 (timer marks failed; banner amber)
    - 401 → exit 1 with clear "missing/invalid API key" message
    - 5xx / timeout → exit 1 (transient, watchdog 24h window catches it)

References:
    https://artificialanalysis.ai/api-reference

Usage:
    python3 scripts/llm_token_index.py --record
    python3 scripts/llm_token_index.py --record --models gpt-4o,claude-opus-4-7
    python3 scripts/llm_token_index.py --dry-run     # prints, no DB write
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ─── Constants ───────────────────────────────────────────────────────

AA_BASE = "https://artificialanalysis.ai/api/v2/data/llms/models"
AA_TIMEOUT_S = 10.0
METHODOLOGY_VERSION = 1

# Default basket. These IDs are guesses based on AA's documented naming
# convention; the runtime tolerates missing entries (logs + skips) so a
# mismatched ID just drops out of the median rather than crashing the
# pipeline. Override at the CLI via --models.
DEFAULT_BASKET = (
    "gpt-4o",
    "claude-opus-4-7",
    "claude-sonnet-4-5",
    "gemini-2-5-pro",
    "deepseek-v3",
    "llama-3-1-405b",
    "mistral-large",
)

DEFAULT_INPUT_WEIGHT = 0.7  # 70/30 input/output blend


# ─── Errors ─────────────────────────────────────────────────────────


class ArtificialAnalysisError(RuntimeError):
    """Any failure pulling or parsing the AA response."""


# ─── Index result ───────────────────────────────────────────────────


@dataclass
class IndexResult:
    """The numbers + provenance for one daily index computation.

    `raw_avg_usd` is the median blended USD-per-Mtok across the basket
    BEFORE normalisation. `components` is the per-model breakdown that
    feeds the median, kept in the row for debugging + future re-derivation
    under a new methodology_version.
    """

    raw_avg_usd: float
    components: dict[str, dict[str, float]] = field(default_factory=dict)


# ─── HTTP ───────────────────────────────────────────────────────────


def fetch_aa_models(api_key: str, *, timeout: float = AA_TIMEOUT_S) -> dict[str, Any]:
    """GET https://artificialanalysis.ai/api/v2/data/llms/models with x-api-key.

    Raises ArtificialAnalysisError on any non-2xx, timeout, or malformed
    JSON. Distinguishes 401 (auth) from 5xx (transient) in the message
    so operator-side debugging is fast.
    """
    if not api_key:
        raise ArtificialAnalysisError(
            "ARTIFICIAL_ANALYSIS_API_KEY is missing. "
            "Sign up at https://artificialanalysis.ai/login + generate "
            "a key, then set ARTIFICIAL_ANALYSIS_API_KEY in .env."
        )

    req = Request(AA_BASE, headers={"x-api-key": api_key, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        if exc.code == 401:
            raise ArtificialAnalysisError(
                "Artificial Analysis API returned 401 — missing or invalid API key. "
                "Check ARTIFICIAL_ANALYSIS_API_KEY in .env."
            ) from exc
        raise ArtificialAnalysisError(
            f"Artificial Analysis API returned HTTP {exc.code}: {exc.reason}"
        ) from exc
    except (URLError, socket.timeout) as exc:
        raise ArtificialAnalysisError(
            f"Artificial Analysis API unreachable: {exc}"
        ) from exc

    try:
        return json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ArtificialAnalysisError(f"AA response was not valid JSON: {exc}") from exc


# ─── Math ───────────────────────────────────────────────────────────


def _blend_per_model(input_per_mtok: float, output_per_mtok: float, *, input_weight: float) -> float:
    """Blend a model's input + output price into a single $/Mtok scalar.

    `input_weight` is the share of input tokens in a typical workload.
    0.7 (default) reflects the read-heavy nature of agentic Claude/
    GPT-4o usage. The complement (1 - input_weight) is the output share.
    """
    output_weight = 1.0 - input_weight
    return input_weight * input_per_mtok + output_weight * output_per_mtok


def _median(values: list[float]) -> float:
    if not values:
        raise ValueError("median() called on empty list")
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def compute_index(
    aa_response: dict[str, Any],
    *,
    model_ids: list[str],
    input_weight: float = DEFAULT_INPUT_WEIGHT,
) -> IndexResult:
    """Build the IndexResult from an AA payload + our basket spec.

    AA returns the model list under either `data` (canonical) or a bare
    list (defensive fallback). We tolerate either shape.

    Models in `model_ids` that aren't in the response are logged + skipped;
    if NONE of the basket matches, that's a hard failure (raises
    ValueError) — there's no useful signal to persist.
    """
    rows = _extract_model_rows(aa_response)
    by_id = {row.get("id"): row for row in rows if isinstance(row, dict) and row.get("id")}

    components: dict[str, dict[str, float]] = {}
    blended: list[float] = []

    for model_id in model_ids:
        row = by_id.get(model_id)
        if row is None:
            logging.info("[llm_token_index] model %s not in AA response — skipping", model_id)
            continue
        pricing = row.get("pricing") or {}
        inp = pricing.get("price_1m_input_tokens")
        out = pricing.get("price_1m_output_tokens")
        if inp is None or out is None:
            logging.info("[llm_token_index] model %s missing pricing fields — skipping", model_id)
            continue
        components[model_id] = {
            "input_per_mtok": float(inp),
            "output_per_mtok": float(out),
            "weight": 1.0,
        }
        blended.append(_blend_per_model(float(inp), float(out), input_weight=input_weight))

    if not blended:
        raise ValueError(
            f"None of the requested model IDs were found in the AA response. "
            f"Requested: {model_ids}"
        )

    return IndexResult(raw_avg_usd=_median(blended), components=components)


def _extract_model_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """AA's documented shape is `{status, data: [...]}`; accept top-level list too."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
    if isinstance(payload, list):
        return payload
    return []


# ─── Normalisation ──────────────────────────────────────────────────


def normalize_against_base(raw_avg_usd: float, base_raw: Optional[float]) -> float:
    """Project today's raw onto the historical base (first persisted day).

    `base_raw is None` → no prior history, today IS the base, return 1.0.
    `base_raw == 0`    → defensive guard, treat as reset, return 1.0.
    """
    if base_raw is None or base_raw == 0:
        return 1.0
    return raw_avg_usd / base_raw


# ─── CLI entry ──────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--record", action="store_true", help="Persist the result to Turso")
    parser.add_argument("--dry-run", action="store_true", help="Compute + print, do not write DB")
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_BASKET),
        help="Comma-separated AA model IDs (overrides default basket)",
    )
    parser.add_argument(
        "--input-weight",
        type=float,
        default=DEFAULT_INPUT_WEIGHT,
        help="Share of input tokens in the blend (default 0.7 = 70/30 input/output)",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="UTC date override YYYY-MM-DD (default: today UTC). Useful for backfill.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _load_dotenv_if_present()

    api_key = os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", "").strip()
    if not api_key:
        logging.error(
            "ARTIFICIAL_ANALYSIS_API_KEY is missing. Sign up at "
            "https://artificialanalysis.ai/login + add the key to .env."
        )
        return 1

    model_ids = [m.strip() for m in args.models.split(",") if m.strip()]
    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        payload = fetch_aa_models(api_key)
        result = compute_index(payload, model_ids=model_ids, input_weight=args.input_weight)
    except ArtificialAnalysisError as exc:
        logging.error("[llm_token_index] AA fetch failed: %s", exc)
        return 1
    except ValueError as exc:
        logging.error("[llm_token_index] index computation failed: %s", exc)
        return 1

    if args.dry_run:
        logging.info(
            "[llm_token_index] dry-run date=%s raw_avg_usd=%.4f components=%d",
            date_str, result.raw_avg_usd, len(result.components),
        )
        print(json.dumps({
            "date": date_str,
            "raw_avg_usd": result.raw_avg_usd,
            "components": result.components,
        }, indent=2))
        return 0

    if not args.record:
        logging.error("[llm_token_index] pass --record to write to DB or --dry-run to preview.")
        return 1

    # Defer the import so unit tests that monkeypatch writers can stay light.
    from db.service_cycle import service_cycle
    from db.writer import (
        record_llm_token_index,
        get_llm_token_index_base_raw,
        ensure_no_replica_for_writers,
    )

    ensure_no_replica_for_writers()

    # service_cycle (DUR-14) heartbeats ok on clean exit; on failure it
    # writes error (+ retry embargo) BEFORE re-raising into the except
    # below, which converts to the exit code the timer unit expects.
    try:
        with service_cycle("llm-token-index", market_hours_class="daily") as cycle:
            base_raw = get_llm_token_index_base_raw()
            index_value = normalize_against_base(result.raw_avg_usd, base_raw)

            record_llm_token_index(
                date_str=date_str,
                index_value=index_value,
                raw_avg_usd=result.raw_avg_usd,
                components=result.components,
                methodology_version=METHODOLOGY_VERSION,
            )
            cycle.finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        logging.info(
            "[llm_token_index] persisted date=%s index_value=%.4f raw_avg_usd=%.4f models=%d",
            date_str, index_value, result.raw_avg_usd, len(result.components),
        )
        return 0
    except Exception as exc:
        logging.exception("[llm_token_index] DB write failed: %s", exc)
        return 1


def _load_dotenv_if_present() -> None:
    """Best-effort: load .env so the script Just Works when run via CLI."""
    project_root = Path(__file__).resolve().parent.parent
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
        load_dotenv(project_root / ".env")
        load_dotenv(project_root / "web" / ".env")
    except ImportError:
        pass


if __name__ == "__main__":
    sys.exit(main())
