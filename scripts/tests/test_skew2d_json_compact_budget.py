"""PERF-067: `data/skew2d.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The checked-in skew history fixture is 733 sessions and serializes
through `build_payload` to 81_861 pretty bytes versus 56_835 compact,
rewritten every `radon-skew2d` cycle including the unchanged-source
heartbeat path.
"""
from __future__ import annotations

import json
from pathlib import Path

import fetch_skew2d as mod

FIXTURES = Path(__file__).parent / "fixtures"


def test_skew2d_json_cache_is_compact(tmp_path, monkeypatch):
    rows = json.loads((FIXTURES / "skew2d_ratios_sample.json").read_text())["rows"]
    series = mod.compute_change_2d_series(rows)
    payload = mod.build_payload(series, scan_time="2026-11-23T21:50:00Z")
    path = tmp_path / "skew2d.json"
    monkeypatch.setattr(mod, "SKEW2D_JSON", path)
    mod.persist_json(payload)

    raw = path.read_bytes()
    compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pretty = json.dumps(payload, indent=2).encode("utf-8")
    assert json.loads(raw) == payload
    assert len(series) == 733
    assert len(pretty) > len(compact)
    assert len(raw) == len(compact), (
        f"cache bytes {len(raw)} compact {len(compact)} pretty {len(pretty)}"
    )
    assert raw == compact
