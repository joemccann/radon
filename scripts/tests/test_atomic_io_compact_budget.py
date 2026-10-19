"""PERF-017: `atomic_save` must persist compact JSON (no indent).

Pretty-printed `indent=2` inflated every crash-safe JSON fallback that
goes through `utils.atomic_io` (gex, breadth, journal trade_log,
price-history cache, calm-streak, vix-model-spread disk fallback).
A 400-level GEX-shaped payload is 41_971 pretty bytes versus 23_584
compact. Checksum stays canonical compact JSON; only the on-disk dump
changes.
"""
from __future__ import annotations

import json
from pathlib import Path

from utils.atomic_io import atomic_save, verified_load


def test_atomic_save_persists_compact_json(tmp_path: Path):
    payload = {
        "ticker": "SPX",
        "levels": [{"strike": 5000 + i, "gex": float(i)} for i in range(400)],
    }
    path = tmp_path / "gex.json"
    atomic_save(str(path), payload)

    raw = path.read_bytes()
    written = json.loads(raw)
    compact = json.dumps(written, separators=(",", ":")).encode("utf-8")
    pretty = json.dumps(written, indent=2).encode("utf-8")
    assert verified_load(str(path)) == payload
    assert len(pretty) > len(compact)
    assert len(raw) == len(compact), (
        f"cache bytes {len(raw)} compact {len(compact)} pretty {len(pretty)}"
    )
    assert raw == compact
