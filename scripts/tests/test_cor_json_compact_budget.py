"""PERF-026: `data/cor.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The checked-in 5_181-session Cboe fixture serializes to 662_137
pretty bytes versus 402_743 compact, rewritten every `radon-cor` cycle
even on the 304 heartbeat path.
"""
from __future__ import annotations

import json
from pathlib import Path

import fetch_cor as mod

FIXTURES = Path(__file__).parent / "fixtures"
COR_CSV = {
    tenor: (FIXTURES / f"cor_sample_{tenor[3:].lower()}.csv").read_text()
    for tenor in ("cor1m", "cor3m", "cor6m", "cor1y")
}


def test_cor_json_cache_is_compact(tmp_path, monkeypatch):
    series = mod.merge_series({k: mod.parse_index_csv(v) for k, v in COR_CSV.items()})
    payload = {
        "scan_time": "2026-10-25T02:21:44Z",
        "source_last_modified": {
            "cor1m": "Fri, 24 Oct 2026 22:31:04 GMT",
            "cor3m": "Fri, 24 Oct 2026 22:31:10 GMT",
            "cor6m": "Fri, 24 Oct 2026 22:31:16 GMT",
            "cor1y": "Fri, 24 Oct 2026 22:31:22 GMT",
        },
        "count": len(series),
        "current": mod._build_current(series),
        "stats": mod.compute_stats(series),
        "series": series,
    }
    path = tmp_path / "cor.json"
    monkeypatch.setattr(mod, "COR_JSON", path)
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
