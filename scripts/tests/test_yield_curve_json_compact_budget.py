"""PERF-036: `data/yield_curve.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The 2026 Treasury fixture tiled across FIRST_YEAR..2026 (5_402
sessions) serializes to 837_503 pretty bytes versus 524_130 compact,
rewritten every `radon-yield-curve` cycle even on the unchanged-source
heartbeat path.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import fetch_yield_curve as mod

FIXTURES = Path(__file__).parent / "fixtures"
CSV = (FIXTURES / "yield_curve_2026_sample.csv").read_text()


def _production_length_series() -> list[dict]:
    base = mod.parse_treasury_csv(CSV)
    rows: list[dict] = []
    for year in range(mod.FIRST_YEAR, 2027):
        for row in base:
            candidate = {**row, "date": f"{year}{row['date'][4:]}"}
            try:
                date.fromisoformat(candidate["date"])
            except ValueError:
                continue
            rows.append(candidate)
    return mod.build_series(rows, None)


def test_yield_curve_json_cache_is_compact(tmp_path, monkeypatch):
    payload = mod.build_output(
        _production_length_series(),
        scan_time="2026-10-30T22:32:38Z",
    )
    path = tmp_path / "yield_curve.json"
    monkeypatch.setattr(mod, "YIELD_CURVE_JSON", path)
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
