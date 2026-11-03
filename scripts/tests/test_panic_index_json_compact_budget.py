"""PERF-039: `data/panic_index.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the twice-daily dual-write inflated the
fallback file. The Cboe fixture tiled across 4_268 sessions (the spec's
real join length) serializes to 1_214_326 pretty bytes versus 761_575
compact, rewritten every `radon-panic-index` cycle including the 304
unchanged-source heartbeat path.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import fetch_panic_index as mod
from fetch_vixts import parse_index_csv
from lib.panic_index_math import attach_delta, attach_level, attach_z_scores, join_series

FIXTURES = Path(__file__).parent / "fixtures"
TARGET_SESSIONS = 4268  # lib.panic_index_math: "the real join is ~4,268"


def _production_length_series() -> tuple[list[dict], list[str]]:
    series, dropped, _base = join_series(
        parse_index_csv((FIXTURES / "panic_index_vix_sample.csv").read_text(), "CLOSE"),
        parse_index_csv((FIXTURES / "panic_index_vix3m_sample.csv").read_text(), "CLOSE"),
        parse_index_csv((FIXTURES / "panic_index_vvix_sample.csv").read_text(), "VVIX"),
        parse_index_csv((FIXTURES / "panic_index_skew_sample.csv").read_text(), "SKEW"),
    )
    attach_z_scores(series)
    attach_level(series)
    attach_delta(series)
    days: list[dict] = []
    cursor = date(2010, 1, 4)
    i = 0
    while len(days) < TARGET_SESSIONS:
        if cursor.weekday() < 5:
            days.append({**series[i % len(series)], "date": cursor.isoformat()})
            i += 1
        cursor += timedelta(days=1)
    return days, dropped


def test_panic_index_json_cache_is_compact(tmp_path, monkeypatch):
    series, dropped = _production_length_series()
    payload = mod.build_payload(
        series,
        scan_time="2026-11-01T02:50:00Z",
        source_last_modified={"vix": "Fri, 30 Oct 2026 22:31:04 GMT"},
        dropped_dates=dropped,
    )
    path = tmp_path / "panic_index.json"
    monkeypatch.setattr(mod, "PANIC_INDEX_JSON", path)
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
