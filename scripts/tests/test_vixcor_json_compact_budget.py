"""PERF-030: `data/vixcor.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The checked-in VIX + COR3M fixtures join to 5_171 sessions and
serialize to 813_608 pretty bytes versus 549_392 compact, rewritten every
`radon-vixcor` cycle even on the 304 heartbeat path.
"""
from __future__ import annotations

import json
from pathlib import Path

import fetch_vixcor as mod

FIXTURES = Path(__file__).parent / "fixtures"
VIX_CSV = (FIXTURES / "vix_history_sample.csv").read_text()
COR3M_ROWS = json.loads((FIXTURES / "cor3m_sample.json").read_text())
COR3M_PARENT_ROWS = [
    {"date": row["date"], "value": row["cor3m"]}
    for row in COR3M_ROWS
    if row["cor3m"] is not None
]


def test_vixcor_json_cache_is_compact(tmp_path, monkeypatch):
    joined = mod.join_series(mod.parse_index_csv(VIX_CSV), COR3M_PARENT_ROWS)
    payload = mod.build_payload(
        joined,
        scan_time="2026-10-27T02:35:13Z",
        source_last_modified={"vix": "Fri, 24 Oct 2026 22:31:04 GMT"},
        status="ok",
        expected_session="2026-10-24",
        lag_sessions=0,
        parent_as_of=joined[-1]["date"],
        vix_as_of=joined[-1]["date"],
        market_status="closed",
    )
    path = tmp_path / "vixcor.json"
    monkeypatch.setattr(mod, "VIXCOR_JSON", path)
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
