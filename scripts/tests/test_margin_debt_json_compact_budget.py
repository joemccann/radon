"""PERF-061: `data/margin_debt.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The checked-in FINRA workbook spliced with the NYSE legacy series
is 809 months and serializes to 281_846 pretty bytes versus 202_481
compact, rewritten every `radon-margin-debt` cycle including the
unchanged-source heartbeat path.
"""
from __future__ import annotations

import json
from pathlib import Path

import fetch_margin_debt as mod

FIXTURES = Path(__file__).parent / "fixtures"


def _production_payload(monkeypatch) -> dict:
    monkeypatch.setattr(mod, "_load_normalization_inputs", lambda: ({}, {}, False))
    monkeypatch.setattr(mod, "_fetch_spx_monthly_closes", lambda: {})
    xlsx = (FIXTURES / "margin_statistics_finra.xlsx").read_bytes()
    series, available = mod.build_series(xlsx)
    latest = series[-1]
    return {
        "scan_time": "2026-11-17T21:00:00Z",
        "source_last_modified": "Tue, 16 Jun 2026 14:52:10 GMT",
        "count": len(series),
        "splice": {
            "legacy_source": "nyse_legacy",
            "ratio": mod.NYSE_TO_FINRA_SPLICE_RATIO,
            "first_finra_month": next(
                (row["date"] for row in series if row["source"] == mod.SOURCE_FINRA),
                None,
            ),
        },
        "normalization": {"available": available},
        "current": {
            "date": latest["date"],
            "level": latest["level"],
            "level_yoy_pct": latest["level_yoy_pct"],
        },
        "series": series,
    }


def test_margin_debt_json_cache_is_compact(tmp_path, monkeypatch):
    payload = _production_payload(monkeypatch)
    path = tmp_path / "margin_debt.json"
    monkeypatch.setattr(mod, "MARGIN_DEBT_JSON", path)
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
