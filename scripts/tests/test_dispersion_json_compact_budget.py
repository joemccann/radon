"""PERF-046: `data/dispersion.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. A 10-year weekday series from the IB daily window floor 2016-08-31
through 2026-11-05 (2_657 raw rows, 2_569 z-scored points) serializes to
502_803 pretty bytes versus 332_894 compact, rewritten every
`radon-dispersion` cycle including the unchanged-source heartbeat path.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import fetch_dispersion as mod
from lib.dispersion_math import build_payload

START = date(2016, 8, 31)
END = date(2026, 11, 5)


def _production_length_rows() -> list[dict]:
    rows: list[dict] = []
    cursor = START
    i = 0
    while cursor <= END:
        if cursor.weekday() < 5:
            rows.append(
                {
                    "date": cursor.isoformat(),
                    "vix_close": 12.0 + (i % 47) * 0.25 + (i // 250) * 0.05,
                    "stock_spread": 0.015 + (i % 31) * 0.001 + (i // 400) * 0.0002,
                    "sector_spread": 0.008 + (i % 23) * 0.0004 + (i // 300) * 0.0001,
                    "n_stocks": 450,
                    "n_sectors": 11,
                }
            )
            i += 1
        cursor += timedelta(days=1)
    return rows


def test_dispersion_json_cache_is_compact(tmp_path, monkeypatch):
    payload = build_payload(
        _production_length_rows(),
        scan_time="2026-11-05T23:20:33Z",
        status="ok",
        source={"vix": "ib", "stocks": "ib", "sectors": "ib"},
        universe={"n_stocks": 450, "n_sectors": 11},
        fetch={"duration": "10 Y"},
    )
    path = tmp_path / "dispersion.json"
    monkeypatch.setattr(mod, "DISPERSION_JSON", path)
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
