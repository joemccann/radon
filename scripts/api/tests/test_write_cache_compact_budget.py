"""PERF-007: `_write_cache` must persist compact JSON (no indent).

Pretty-printed `indent=2` inflated every scanner/flow JSON fallback on disk
(gex, vcg, cri, flow reports, performance). Compact separators keep the same
object with fewer bytes; readers still `json.loads`.
"""
from __future__ import annotations

import json
from pathlib import Path

from api import server


def test_write_cache_persists_compact_json(tmp_path: Path):
    payload = {
        "ticker": "SPX",
        "levels": [{"strike": 5000 + i, "gex": float(i)} for i in range(400)],
    }
    path = tmp_path / "gex.json"
    server._write_cache(path, payload)

    raw = path.read_bytes()
    compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pretty = json.dumps(payload, indent=2).encode("utf-8")
    assert json.loads(raw) == payload
    assert len(pretty) > len(compact)
    assert len(raw) == len(compact), (
        f"cache bytes {len(raw)} compact {len(compact)} pretty {len(pretty)}"
    )
    assert raw == compact
