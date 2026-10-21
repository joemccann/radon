"""PERF-021: `data/equibles_ats_venue_share.json` must persist compact JSON.

Pretty-printed `indent=2` on the weekly dual-write inflates the fallback
file. A 40-ticker × 52-week core-walk payload (the scheduled watchlist
size, one z-window of derived weekly rows) is 1_601_776 pretty bytes
versus 1_191_063 compact, rewritten every `radon-equibles-ats` cycle.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import fetch_equibles_ats_venue_share as mod


def _core_walk_payload() -> dict:
    """Production-shaped core walk: 40 names × 52 derived weekly rows."""
    anchor = date(2026, 10, 13)  # Monday
    n_tickers = 40
    n_weeks = 52
    series_by_ticker: dict[str, list[dict]] = {}
    for t in range(n_tickers):
        rows = []
        for w in range(n_weeks):
            week = (anchor - timedelta(weeks=n_weeks - 1 - w)).isoformat()
            rows.append(
                {
                    "week_start_date": week,
                    "ats_volume": 4_000_000.0 + t + w,
                    "ats_trade_count": 8_000.0,
                    "non_ats_otc_volume": 6_000_000.0,
                    "non_ats_otc_trade_count": 30_000.0,
                    "total_off_exchange_volume": 10_000_000.0,
                    "ats_share_pct": 40.0,
                    "avg_ats_print_size": 500.0,
                    "avg_non_ats_print_size": 200.0,
                    "short_volume_pct": 40.0,
                    "consolidated_volume": 25_000_000.0,
                    "consolidated_volume_source": "prices",
                    "off_exchange_share_pct": 40.0,
                }
            )
        scored = mod.attach_zscores(rows)
        classified = []
        for row in scored:
            verdict, reason = mod.classify(
                row.get("ats_share_z"),
                row.get("avg_ats_print_size_z"),
                row.get("z_observations", 0),
            )
            classified.append(
                {**row, "classification": verdict, "classification_reason": reason}
            )
        series_by_ticker[f"T{t:03d}"] = classified
    return mod.build_payload(series_by_ticker, "2026-10-20T09:15:00Z", [])


def test_ats_venue_share_json_cache_is_compact(tmp_path: Path, monkeypatch):
    payload = _core_walk_payload()
    path = tmp_path / "equibles_ats_venue_share.json"
    monkeypatch.setattr(mod, "ATS_VENUE_SHARE_JSON", path)
    mod._write_json_cache(payload)

    raw = path.read_bytes()
    compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pretty = json.dumps(payload, indent=2).encode("utf-8")
    assert json.loads(raw) == payload
    assert len(pretty) > len(compact)
    assert len(raw) == len(compact), (
        f"cache bytes {len(raw)} compact {len(compact)} pretty {len(pretty)}"
    )
    assert raw == compact
