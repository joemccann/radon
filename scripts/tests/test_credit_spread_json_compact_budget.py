"""PERF-057: `data/credit_spread.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The checked-in HYG + SPX Yahoo fixtures join to 658 sessions and
serialize to 74_778 pretty bytes versus 52_333 compact, rewritten every
`radon-credit-spread` cycle including the unchanged-source heartbeat path.
"""
from __future__ import annotations

import json
from pathlib import Path

import fetch_credit_spread as mod

FIXTURES = Path(__file__).parent / "fixtures"


def test_credit_spread_json_cache_is_compact(tmp_path, monkeypatch):
    aligned = mod.align_series(
        mod.parse_yahoo_chart((FIXTURES / "credit_spread_hyg_sample.json").read_text()),
        mod.parse_yahoo_chart((FIXTURES / "credit_spread_spx_sample.json").read_text()),
    )
    payload = mod.build_output(aligned, source="ib")
    path = tmp_path / "credit_spread.json"
    monkeypatch.setattr(mod, "CREDIT_SPREAD_JSON", path)
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
