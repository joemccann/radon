"""PERF-066: `data/divyield.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The documented series is monthly from 1990 plus daily rows since the
2026-08-23 cutover (508 rows here) and serializes to 79_412 pretty
bytes versus 49_511 compact, rewritten every `radon-divyield` cycle
including the unchanged-day heartbeat path.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, timedelta

import fetch_divyield as mod


def _series() -> list[dict]:
    rows: list[dict] = []
    year, month = 1990, 1
    while (year, month) <= (2026, 11):
        last = calendar.monthrange(year, month)[1]
        rows.append(
            {
                "date": f"{year:04d}-{month:02d}-{last:02d}",
                "pct_above": 12.15,
                "count_above": 58,
                "total": 480,
                "y10": 8.21,
                "approximate": 1,
            }
        )
        month += 1
        if month > 12:
            month = 1
            year += 1
    cursor = date(2026, 8, 24)
    end = date(2026, 11, 20)
    while cursor <= end:
        if cursor.weekday() < 5:
            rows.append(
                {
                    "date": cursor.isoformat(),
                    "pct_above": 3.78,
                    "count_above": 19,
                    "total": 503,
                    "y10": 4.74,
                    "approximate": 0,
                }
            )
        cursor += timedelta(days=1)
    return rows


def test_divyield_json_cache_is_compact(tmp_path, monkeypatch):
    series = _series()
    yields = {f"T{i:03d}": 1.5 + (i % 7) * 0.4 for i in range(503)}
    payload = mod.build_output(
        yields,
        4.74,
        "2026-11-20",
        "2026-11-20",
        "2026-11-23T22:40:11+00:00",
        {"constituents": "github-datasets", "constituents_count": 503, "quote_errors": 0},
        series,
        "2026-08-23",
    )
    path = tmp_path / "divyield.json"
    monkeypatch.setattr(mod, "DIVYIELD_JSON", path)
    mod._write_json_cache(payload)

    raw = path.read_bytes()
    compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pretty = json.dumps(payload, indent=2).encode("utf-8")
    assert json.loads(raw) == payload
    assert len(pretty) > len(compact)
    assert len(raw) == len(compact), (
        f"cache bytes {len(raw)} compact {len(compact)} pretty {len(pretty)} rows {len(series)}"
    )
    assert raw == compact
