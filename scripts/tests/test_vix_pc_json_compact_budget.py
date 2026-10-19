"""PERF-018: `data/vix-pc.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The checked-in 500-session UW fixture serializes to 101_072 pretty
bytes versus 71_884 compact, rewritten every `radon-vix-pc` cycle.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import fetch_vix_pc as mod

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = json.loads((FIXTURES / "vix_pc_sample.json").read_text())


def test_vix_pc_json_cache_is_compact(tmp_path, monkeypatch):
    rows = mod.parse_uw_rows({"data": SAMPLE["data"]})
    series = mod.compute_series(rows)
    payload = mod.build_payload(
        series,
        scan_time="2026-10-18T02:00:00Z",
        status="ok",
        source="uw",
        now=datetime(2026, 10, 18, 2, tzinfo=timezone.utc),
    )
    path = tmp_path / "vix-pc.json"
    monkeypatch.setattr(mod, "VIX_PC_JSON", path)
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
