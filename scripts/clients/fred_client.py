"""FRED DGS3MO client — annualised 3-month T-bill risk-free rate.

Plan §4.2-4.3: Sharpe/Sortino use FRED DGS3MO daily series, not 0.
Cache: ``data/risk_free_rate.json``  { "YYYY-MM-DD": float_annual_decimal }
FRED returns percent strings (e.g. "5.32" means 5.32% -> 0.0532).

Graceful degradation:
- Missing ``FRED_API_KEY`` -> fallback rate 0.0 with warning.
- Network failure -> try disk cache, else 0.0 with warning.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_PATH = _PROJECT_ROOT / "data" / "risk_free_rate.json"

SERIES_ID = "DGS3MO"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def _load_cache() -> dict[str, float]:
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text())
        if isinstance(data, dict):
            # Support both {date: rate} and {observations: [...]} legacy
            if "observations" in data:
                return {k: float(v) for k, v in data.get("values", {}).items() if _is_finite(v)}
            return {k: float(v) for k, v in data.items() if _is_finite(v) and not k.startswith("_")}
        return {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _is_finite(v: Any) -> bool:
    try:
        f = float(v)
        return f == f and f not in (float("inf"), float("-inf"))
    except (TypeError, ValueError):
        return False


def _save_cache(values: dict[str, float]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write via tmp + replace
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(values, indent=2, sort_keys=True))
        tmp.replace(CACHE_PATH)
    except OSError:
        pass  # best-effort


def _fetch_fred_observations(series_id: str, api_key: str) -> dict[str, float]:
    """Fetch FRED observations -> {YYYY-MM-DD: decimal_rate}.

    Returns decimal (0.0532 for 5.32%). Skips "." missing values.
    Raises on HTTP error.
    """
    # Prefer requests if available (matches fetch_margin_debt.py), else urllib
    try:
        import requests  # type: ignore

        resp = requests.get(
            FRED_URL,
            params={"series_id": series_id, "api_key": api_key, "file_type": "json"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
    except ImportError:
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen

        params = urlencode({"series_id": series_id, "api_key": api_key, "file_type": "json"})
        req = Request(f"{FRED_URL}?{params}", headers={"User-Agent": "radon/fred_client"})
        with urlopen(req, timeout=30) as r:  # noqa: S310
            payload = json.loads(r.read().decode("utf-8"))

    out: dict[str, float] = {}
    for obs in payload.get("observations", []):
        date = str(obs.get("date", ""))[:10]
        val = obs.get("value")
        if not date or val in (None, "", "."):
            continue
        try:
            pct = float(val)
            out[date] = pct / 100.0
        except (TypeError, ValueError):
            continue
    return out


def fetch_dgs3mo_series(api_key: str | None = None, *, save: bool = True) -> dict[str, float]:
    """Fetch DGS3MO annual decimal series from FRED. Returns {date: rate}.

    If *api_key* is None, reads ``FRED_API_KEY`` from env.
    On success and *save*, merges into ``data/risk_free_rate.json``.
    Raises RuntimeError if no key, or propagates HTTP errors.
    """
    key = api_key or os.environ.get("FRED_API_KEY") or os.environ.get("FRED_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY not set")
    values = _fetch_fred_observations(SERIES_ID, key)
    if save and values:
        cached = _load_cache()
        cached.update(values)
        _save_cache(cached)
    return values


def get_risk_free_rate(
    as_of: str | None = None,
    *,
    api_key: str | None = None,
) -> tuple[float, str | None]:
    """Return (annual_rate_decimal, warning_or_None).

    Resolution order:
    1. Live FRED fetch if key available (best-effort, short timeout).
    2. Disk cache ``data/risk_free_rate.json`` most-recent entry <= as_of.
    3. Fallback 0.0 with warning.

    *as_of* is ``YYYY-MM-DD``; if None uses most recent cached value.
    Never raises.
    """
    # Attempt live fetch best-effort (non-blocking for caller is responsibility of builder;
    # here we do a short attempt and swallow errors).
    try:
        live = fetch_dgs3mo_series(api_key=api_key, save=True)
        if live:
            if as_of and as_of in live:
                return live[as_of], None
            # return most recent live value
            latest = sorted(live.keys())[-1]
            return live[latest], None
    except Exception as exc:  # noqa: BLE001
        # Log to stderr but don't fail — fall through to cache
        print(f"[fred] FRED fetch failed: {exc}", file=sys.stderr)

    cached = _load_cache()
    if cached:
        if as_of and as_of in cached:
            return cached[as_of], None
        # Most recent date <= as_of, else overall latest
        if as_of:
            candidates = [d for d in cached if d <= as_of]
            if candidates:
                latest = max(candidates)
                return cached[latest], None
        latest = max(cached.keys())
        # If cache is stale (>30 days), add warning but still use it
        try:
            age_days = (datetime.now(timezone.utc).date() - datetime.strptime(latest, "%Y-%m-%d").date()).days
            if age_days > 30:
                return cached[latest], f"Rf from cached FRED {latest} ({age_days}d stale)"
        except Exception:
            pass
        return cached[latest], None

    return 0.0, "Rf unavailable — FRED_API_KEY not set or fetch failed; Sharpe = excess over 0"


def get_series_for_metrics(
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, float]:
    """Return filtered DGS3MO series for metric window, or {} if unavailable."""
    cached = _load_cache()
    if not cached and os.environ.get("FRED_API_KEY"):
        try:
            cached = fetch_dgs3mo_series(save=True)
        except Exception:
            cached = {}
    if start_date or end_date:
        return {d: v for d, v in cached.items() if (not start_date or d >= start_date) and (not end_date or d <= end_date)}
    return cached
