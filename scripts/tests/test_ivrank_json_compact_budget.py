"""PERF-065: `data/ivrank.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the 5-minute dual-write inflated the fallback
file. Five copies of the checked-in 251-bar IB IV fixture (1_255 sessions,
the documented ~1_260-session 5Y window) serialize through `build_payload`
to 164_003 pretty bytes versus 111_131 compact, rewritten every
`radon-ivrank` cycle including the unchanged-source heartbeat path.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import fetch_ivrank as mod

FIXTURES = Path(__file__).parent / "fixtures"
COPIES = 5


def _five_year_rows() -> list[dict]:
    closes = [
        bar["close"]
        for bar in json.loads((FIXTURES / "iv_rank_ib_sample.json").read_text())["bars"]
    ]
    ivs = closes * COPIES
    rows: list[dict] = []
    cursor = date(2021, 11, 23)
    index = 0
    while index < len(ivs):
        if cursor.weekday() < 5:
            rows.append({"date": cursor.isoformat(), "iv": ivs[index], "source": "ib"})
            index += 1
        cursor += timedelta(days=1)
    return rows


def test_ivrank_json_cache_is_compact(tmp_path, monkeypatch):
    series = mod.compute_series(_five_year_rows())
    payload = mod.build_payload(
        series,
        scan_time="2026-11-23T21:10:00Z",
        status="ok",
        source="ib",
        uw_check=None,
        now=datetime(2026, 11, 23, 21, 10, tzinfo=timezone.utc),
    )
    payload["outliers_repaired"] = []
    path = tmp_path / "ivrank.json"
    monkeypatch.setattr(mod, "IVRANK_JSON", path)
    mod.persist_json(payload)

    raw = path.read_bytes()
    compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pretty = json.dumps(payload, indent=2).encode("utf-8")
    assert json.loads(raw) == payload
    assert len(series) == 251 * COPIES
    assert len(pretty) > len(compact)
    assert len(raw) == len(compact), (
        f"cache bytes {len(raw)} compact {len(compact)} pretty {len(pretty)}"
    )
    assert raw == compact
