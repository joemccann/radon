"""PERF-027: `data/vixts.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The checked-in VIX / VIX3M / SPX Cboe fixtures join to 4_252
sessions and serialize to 538_936 pretty bytes versus 326_199 compact,
rewritten every `radon-vixts` cycle even on the 304 heartbeat path.
"""
from __future__ import annotations

import json
from pathlib import Path

import fetch_vixts as mod
from lib.vixts_math import join_series

FIXTURES = Path(__file__).parent / "fixtures"


def test_vixts_json_cache_is_compact(tmp_path, monkeypatch):
    vix = mod.parse_index_csv((FIXTURES / "vix_history_sample.csv").read_text(), "CLOSE")
    vix3m = mod.parse_index_csv((FIXTURES / "vixts_vix3m_sample.csv").read_text(), "CLOSE")
    spx = mod.parse_index_csv((FIXTURES / "spx_history_sample.csv").read_text(), "SPX")
    series = join_series(vix, vix3m, spx)
    payload = mod.build_payload(
        series,
        scan_time="2026-10-25T02:45:40Z",
        source_last_modified={
            "vix": "Fri, 24 Oct 2026 22:00:00 GMT",
            "vix3m": "Fri, 24 Oct 2026 22:00:00 GMT",
            "spx": "Fri, 24 Oct 2026 22:00:00 GMT",
        },
    )
    path = tmp_path / "vixts.json"
    monkeypatch.setattr(mod, "VIXTS_JSON", path)
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
