"""PERF-052: radon-refresh must persist compact JSON (no indent).

Pretty-printed `indent=2` on the 15-minute CRI / VCG / GEX rewrite inflated
the disk fallback and overwrote `gex_scan.persist_snapshot`'s compact
`atomic_save`. A 400-level GEX-shaped payload is 21_529 pretty bytes versus
11_117 compact, rewritten every `radon-refresh` fire (Mon-Fri 13:00-21:45 UTC
on :00/:15/:30/:45).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import data_refresh as dr


def test_run_scan_writes_compact_json(tmp_path, monkeypatch):
    payload = {
        "ticker": "SPX",
        "levels": [{"strike": 5000 + i, "gex": float(i)} for i in range(400)],
    }
    out = tmp_path / "gex.json"
    monkeypatch.setattr(dr, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(dr, "_SCRIPTS_DIR", tmp_path)
    (tmp_path / "gex_scan.py").write_text("# stub\n")
    result = MagicMock(
        returncode=0,
        stdout=json.dumps(payload, indent=2),
        stderr="",
    )

    with patch.object(dr.subprocess, "run", return_value=result):
        ok = dr._run_scan("gex_scan.py", ["--json", "--no-mq"], out)

    assert ok is True
    raw = out.read_bytes()
    compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pretty = json.dumps(payload, indent=2).encode("utf-8")
    assert json.loads(raw) == payload
    assert len(pretty) > len(compact)
    assert len(raw) == len(compact), (
        f"cache bytes {len(raw)} compact {len(compact)} pretty {len(pretty)}"
    )
    assert raw == compact
