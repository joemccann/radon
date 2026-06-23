"""Regression: every scan writer must emit timezone-aware ISO timestamps.

Hetzner runs UTC, so naive `datetime.now().isoformat()` strings became
the source-of-truth for `scan_time`/`analysis_time`/`taken_at`. JS
`new Date()` parses naive ISO strings as *local* time, shifting the
resulting instant by the user's TZ offset and rolling the trading-day
forward — causing wrong-day filtering and premature staleness in the
dashboard.

These tests pin the writer side: the emitted ISO must include an
explicit UTC offset (`+00:00`) or a `Z` suffix.
"""
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _is_tz_aware(iso: str) -> bool:
    return iso.endswith("Z") or "+" in iso[10:] or iso.endswith("-00:00")


# ── vcg_scan.build_json_output ───────────────────────────────────

def test_vcg_scan_time_is_timezone_aware():
    from vcg_scan import build_json_output

    n = 25
    model = {
        "residuals": np.zeros(n),
        "vcg": np.zeros(n),
        "vcg_div": np.zeros(n),
        "beta1": np.full(n, -0.1),
        "beta2": np.full(n, -0.1),
        "vix_levels": np.full(n, 20.0),
        "vvix_levels": np.full(n, 100.0),
        "credit_levels": np.full(n, 50.0),
    }
    dates = [f"2026-04-{i+1:02d}" for i in range(n + 1)]
    signal = {"vcg": 0.0, "tier": None, "ro": False, "edr": False, "bounce": False}

    result = build_json_output(
        signal=signal,
        model=model,
        dates=dates,
        proxy="HYG/IEI",
        market_open=False,
    )

    scan_time = result["scan_time"]
    assert isinstance(scan_time, str) and scan_time
    assert _is_tz_aware(scan_time), (
        f"vcg scan_time {scan_time!r} is naive — JS parses it as local time"
    )


# ── flow_analysis.run_analysis ───────────────────────────────────

def test_flow_analysis_time_is_timezone_aware(monkeypatch):
    """Empty-portfolio path emits an analysis_time."""
    from flow_analysis import run_analysis

    monkeypatch.setattr("flow_analysis.load_portfolio", lambda: [])

    buf = io.StringIO()
    with redirect_stdout(buf):
        run_analysis()

    output = json.loads(buf.getvalue())
    analysis_time = output["analysis_time"]
    assert isinstance(analysis_time, str) and analysis_time
    assert _is_tz_aware(analysis_time), (
        f"flow analysis_time {analysis_time!r} is naive — JS parses it as local time"
    )


# ── cri_scan: source-level pin ───────────────────────────────────
# cri_scan's main() is too large to invoke in a unit test (network,
# IB, CBOE downloads). Pin the writer line at the source level: the
# `scan_time` emission MUST go through `datetime.now(timezone.utc)`
# rather than `datetime.now()`.

def test_cri_scan_emits_tz_aware_scan_time_at_source():
    src = Path(__file__).resolve().parent.parent / "cri_scan.py"
    text = src.read_text()
    # Find the unique line that builds the output's `scan_time`.
    matches = [
        line for line in text.splitlines()
        if '"scan_time": datetime.now' in line and "isoformat" in line
    ]
    assert matches, "Couldn't find cri_scan scan_time emission"
    for line in matches:
        assert "timezone.utc" in line or "tz.utc" in line, (
            f"naive scan_time emission still present in cri_scan.py: {line!r}"
        )
