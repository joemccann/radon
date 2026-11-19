"""The regime share cards must show the values the page shows.

2026-11-19: /regime/cri showed CRI 51 (HIGH) recomputed from live VIX 28.54 /
VVIX 127.56 / COR1M 30.70, while the share cards rendered the 12:00 scan
snapshot (CRI 48, COR1M 23.2). The page now posts its live reading, and the
generator overlays it on the snapshot before rendering.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_regime_share as gen  # noqa: E402

SNAPSHOT = {
    "date": "2026-11-19",
    "vix": 27.5,
    "vvix": 125.1,
    "spy": 718.0,
    "cor1m": 23.2,
    "cor1m_5d_change": 5.0,
    "vvix_vix_ratio": 4.5,
    "spx_distance_pct": -5.4,
    "spx_100d_ma": 759.97,
    "realized_vol": 23.0,
    "vix_5d_roc": 40.0,
    "cri": {"score": 48, "level": "ELEVATED",
            "components": {"vix": 16.2, "vvix": 12.5, "correlation": 5.2, "momentum": 14.0}},
    "crash_trigger": {"triggered": False, "conditions": {
        "spx_below_100d_ma": True, "realized_vol_gt_25": False, "cor1m_gt_60": False}},
    "cta": {"keep": "me"},
}

LIVE = {
    "vix": 28.54,
    "vvix": 127.56,
    "spy": 716.52,
    "cor1m": 30.70,
    "cor1m_5d_change": 13.04,
    "vvix_vix_ratio": 4.47,
    "spx_distance_pct": -5.72,
    "realized_vol": 23.48,
    "cri": {"score": 51, "level": "HIGH",
            "components": {"vix": 16.5, "vvix": 12.8, "correlation": 7.4, "momentum": 14.3}},
    "crash_trigger": {"triggered": False, "conditions": {"spx_below_100d_ma": True, "cor1m_gt_60": False}},
}


def test_overlay_replaces_the_live_readings():
    merged = gen.apply_live_overlay(SNAPSHOT, LIVE)
    assert merged["cri"] == LIVE["cri"]
    for key in ("vix", "vvix", "spy", "cor1m", "cor1m_5d_change", "vvix_vix_ratio",
                "spx_distance_pct", "realized_vol"):
        assert merged[key] == LIVE[key], key


def test_overlay_keeps_snapshot_fields_it_does_not_carry():
    merged = gen.apply_live_overlay(SNAPSHOT, LIVE)
    assert merged["cta"] == {"keep": "me"}
    assert merged["vix_5d_roc"] == 40.0
    assert merged["crash_trigger"]["conditions"]["realized_vol_gt_25"] is False
    assert SNAPSHOT["cri"]["score"] == 48, "snapshot must not be mutated"


def test_overlay_ignores_unknown_keys():
    merged = gen.apply_live_overlay(SNAPSHOT, {"date": "1999-01-01", "vix": 30.0})
    assert merged["date"] == "2026-11-19"
    assert merged["vix"] == 30.0


def test_no_overlay_is_the_snapshot():
    assert gen.apply_live_overlay(SNAPSHOT, None) == SNAPSHOT


def test_cli_renders_the_live_score(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "cri.json"
    snapshot_path.write_text(json.dumps(SNAPSHOT))
    overlay_path = tmp_path / "overlay.json"
    overlay_path.write_text(json.dumps(LIVE))
    code = (
        "import sys, json, pathlib;"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r});"
        "import generate_regime_share as g;"
        f"g.load_cri = lambda d=None: json.loads(pathlib.Path({str(snapshot_path)!r}).read_text());"
        "g.screenshot_card = lambda *a, **k: False;"
        f"g.REPORTS_DIR = pathlib.Path({str(tmp_path)!r});"
        f"sys.argv = ['g', '--json', '--no-open', '--live-overlay', {str(overlay_path)!r}];"
        "g.main()"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    result = json.loads(out.stdout[out.stdout.index("{"):])
    card1 = Path(result["card_paths"][0]).read_text()
    assert ">51<" in card1 and "HIGH" in card1
    preview = Path(result["preview_path"]).read_text()
    assert "CRI: 51/100 (HIGH)" in preview
