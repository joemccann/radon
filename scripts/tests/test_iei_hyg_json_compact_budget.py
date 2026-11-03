"""PERF-040: `data/iei_hyg.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The Yahoo IEI/HYG/DXY fixture tiled across weekdays 2007-04-11
(HYG inception / credit-spread period1) through 2026-10-31 (5_103
sessions) serializes to 962_823 pretty bytes versus 707_564 compact,
rewritten every `radon-iei-hyg` cycle even on the unchanged-source
heartbeat path.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import fetch_iei_hyg as mod
from fetch_credit_spread import parse_yahoo_chart

FIXTURES = Path(__file__).parent / "fixtures"
START = date(2007, 4, 11)
END = date(2026, 10, 31)


def _production_length_series() -> list[dict]:
    series = mod.align_series(
        parse_yahoo_chart((FIXTURES / "iei_hyg_iei_sample.json").read_text()),
        parse_yahoo_chart((FIXTURES / "iei_hyg_hyg_sample.json").read_text()),
        parse_yahoo_chart((FIXTURES / "iei_hyg_dxy_sample.json").read_text()),
    )
    days: list[dict] = []
    cursor = START
    i = 0
    while cursor <= END:
        if cursor.weekday() < 5:
            days.append({**series[i % len(series)], "date": cursor.isoformat()})
            i += 1
        cursor += timedelta(days=1)
    return days


def test_iei_hyg_json_cache_is_compact(tmp_path, monkeypatch):
    payload = mod.build_output(
        _production_length_series(),
        scan_time="2026-11-01T21:59:53Z",
        source="ib",
    )
    path = tmp_path / "iei_hyg.json"
    monkeypatch.setattr(mod, "IEI_HYG_JSON", path)
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
