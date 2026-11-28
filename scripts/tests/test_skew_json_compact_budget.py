"""PERF-064: `data/skew.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the 5-minute dual-write inflated the fallback
file. The checked-in skew history fixture is 733 sessions and serializes
to 172_029 pretty bytes versus 123_547 compact, rewritten every
`radon-skew` cycle including the unchanged-source heartbeat path.
"""
from __future__ import annotations

import json
from pathlib import Path

import fetch_skew as mod

FIXTURES = Path(__file__).parent / "fixtures"


def test_skew_json_cache_is_compact(tmp_path, monkeypatch):
    rows = json.loads((FIXTURES / "skew2d_ratios_sample.json").read_text())["rows"]
    series = mod.compute_change_series(rows)
    payload = mod._build_payload(
        "2026-11-23T21:55:00Z", series, market_status="closed"
    )
    path = tmp_path / "skew.json"
    monkeypatch.setattr(mod, "SKEW_JSON", path)
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
