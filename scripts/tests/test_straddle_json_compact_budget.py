"""PERF-054: `data/straddle.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The checked-in Cboe SPX + VIX1D fixtures join to 1_059 sessions and
serialize to 139_560 pretty bytes versus 94_967 compact, rewritten every
`radon-straddle` cycle including the 304 heartbeat path.
"""
from __future__ import annotations

import json
from pathlib import Path

import fetch_straddle as mod

FIXTURES = Path(__file__).parent / "fixtures"


def test_straddle_json_cache_is_compact(tmp_path, monkeypatch):
    series = mod.compute_series(
        mod.parse_index_csv((FIXTURES / "spx_history_sample.csv").read_text(), "SPX"),
        mod.parse_index_csv((FIXTURES / "vix1d_sample.csv").read_text(), "CLOSE"),
    )
    payload = {
        "scan_time": "2026-11-11T02:15:00Z",
        "source_last_modified": {
            "spx": "Tue, 10 Nov 2026 22:00:00 GMT",
            "vix1d": "Tue, 10 Nov 2026 22:00:00 GMT",
        },
        "count": len(series),
        "current": mod._build_current(series),
        "stats": mod.compute_stats(series),
        "series": series,
    }
    path = tmp_path / "straddle.json"
    monkeypatch.setattr(mod, "STRADDLE_JSON", path)
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
