"""PERF-012: `data/vol_cone.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the EOD and live writers nearly doubled the
fallback file. A 25-name slice of the cone was 7_533_662 pretty bytes versus
4_198_391 compact; full universe (~120 names) estimates ~36 MB vs ~20 MB
rewritten every `radon-vol-cone` cycle.
"""
from __future__ import annotations

import json
from pathlib import Path

import fetch_vol_cone as mod


def test_vol_cone_json_cache_is_compact(tmp_path, monkeypatch):
    payload = {
        "scan_time": "2026-10-14T20:45:00Z",
        "source_as_of": "2026-10-14",
        "count": 400,
        "hit_count": 3,
        "current": {"ticker": "NVDA", "atm_iv": 0.38},
        "names": [
            {"ticker": f"T{i:03d}", "expiry": "2026-12-18", "atm_iv": 0.3 + i / 1000.0}
            for i in range(400)
        ],
        "hits": [],
    }
    path = tmp_path / "vol_cone.json"
    monkeypatch.setattr(mod, "VOL_CONE_JSON", path)
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
