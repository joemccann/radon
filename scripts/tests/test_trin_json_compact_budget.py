"""PERF-038: `data/trin.json` must persist compact JSON (no indent).

Pretty-printed `indent=2` on the 5-minute dual-write inflated the fallback
file. A HOURLY_PAYLOAD_CAP hourly window plus 4_032 daily closes
serializes to 298_001 pretty bytes versus 176_255 compact, rewritten
every `radon-trin` cycle including the no-new-sample heartbeat path.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import fetch_trin as mod

_ET = ZoneInfo("America/New_York")


def _hourly_samples(n: int) -> list[dict]:
    samples: list[dict] = []
    day = date(2025, 1, 2)
    while len(samples) < n:
        if day.weekday() < 5:
            for hour in range(10, 16):
                ts = datetime(day.year, day.month, day.day, hour, 0, tzinfo=_ET)
                samples.append(
                    {
                        "ts": ts.isoformat(),
                        "trin": 0.95,
                        "ad": 1.1,
                        "vol": 1.0,
                        "source": "ib",
                    }
                )
                if len(samples) >= n:
                    break
        day += timedelta(days=1)
    return samples


def _daily_rows(n: int) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    day = date(2010, 1, 4)
    while len(rows) < n:
        if day.weekday() < 5:
            rows.append((day.isoformat(), 0.92))
        day += timedelta(days=1)
    return rows


def test_trin_json_cache_is_compact(tmp_path, monkeypatch):
    payload = mod.build_output(
        _hourly_samples(mod.HOURLY_PAYLOAD_CAP),
        _daily_rows(4032),
        scan_time="2026-10-30T21:57:00Z",
    )
    path = tmp_path / "trin.json"
    monkeypatch.setattr(mod, "TRIN_JSON", path)
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
