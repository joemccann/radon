"""PERF-053: `data/iv_spread.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The checked-in 5Y IB paired-close fixture is 1_253 sessions and
serializes to 162_101 pretty bytes versus 109_317 compact, rewritten every
`radon-iv-spread` cycle including the unchanged-source heartbeat path.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import fetch_iv_spread as mod

FIXTURES = Path(__file__).parent / "fixtures"


def test_iv_spread_json_cache_is_compact(tmp_path, monkeypatch):
    closes = json.loads((FIXTURES / "iv_spread_ib_5y_closes.json").read_text())["rows"]
    rows = [
        {"date": row["date"], "spx_iv": row["spx"], "ndx_iv": row["ndx"]}
        for row in closes
    ]
    series = mod.compute_series(rows)
    payload = mod.build_payload(
        series,
        scan_time="2026-11-11T22:15:00Z",
        status="ok",
        source="ib",
        dropped_unpaired=0,
        excluded=[],
        now=datetime(2026, 11, 11, 22, 15, tzinfo=timezone.utc),
    )
    path = tmp_path / "iv_spread.json"
    monkeypatch.setattr(mod, "IV_SPREAD_JSON", path)
    mod.persist_json(payload)

    raw = path.read_bytes()
    compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pretty = json.dumps(payload, indent=2).encode("utf-8")
    assert json.loads(raw) == payload
    assert len(pretty) > len(compact)
    assert len(raw) == len(compact), (
        f"cache bytes {len(raw)} compact {len(compact)} pretty {len(pretty)}"
    )
    assert raw == compact
