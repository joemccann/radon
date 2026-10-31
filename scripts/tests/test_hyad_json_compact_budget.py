"""PERF-037: `data/hyad.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the daily dual-write inflated the fallback
file. The FINRA fixture tiled across BACKFILL_START..2026-10-29 (2_289
sessions) serializes to 381_859 pretty bytes versus 249_020 compact,
rewritten every `radon-hyad` cycle even on the unchanged-day heartbeat
path.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import fetch_hyad as mod

FIXTURES = Path(__file__).parent / "fixtures"
ENVELOPE = json.loads((FIXTURES / "hyad_sample.json").read_text())
END = date(2026, 10, 29)


def _production_length_days() -> list[dict]:
    raw = mod.merge_bond_types(mod.parse_breadth_rows(ENVELOPE))
    days: list[dict] = []
    cursor = date.fromisoformat(mod.BACKFILL_START)
    i = 0
    while cursor <= END:
        if cursor.weekday() < 5:
            src = raw[i % len(raw)]
            days.append({**src, "date": cursor.isoformat()})
            i += 1
        cursor += timedelta(days=1)
    return days


def test_hyad_json_cache_is_compact(tmp_path, monkeypatch):
    days = _production_length_days()
    spx = {row["date"]: 4000.0 + (i % 500) for i, row in enumerate(days)}
    payload = mod.build_output(
        rows=days,
        spx_by_date=spx,
        scan_time="2026-10-30T11:00:57Z",
    )
    path = tmp_path / "hyad.json"
    monkeypatch.setattr(mod, "HYAD_JSON", path)
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
