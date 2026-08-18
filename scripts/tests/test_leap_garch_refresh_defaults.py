"""Scheduled LEAP/GARCH refresh wrappers default to the largecaps universe.

`indexes` adds the Russell 2000 for ~2500 tickers at 3 Unusual Whales
requests each; unattended on that universe the two schedulers spend most of
the 40k daily cap between them.
"""
from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent


def test_leap_refresh_defaults_to_largecaps_and_3610s_curl():
    text = (SCRIPTS / "run_leap_refresh.sh").read_text()
    assert "RADON_LEAP_REFRESH_PRESET:-largecaps}" in text
    assert "curl -fsS -X POST -m 3610" in text


def test_garch_refresh_defaults_to_largecaps_and_3610s_curl():
    text = (SCRIPTS / "run_garch_refresh.sh").read_text()
    assert "RADON_GARCH_REFRESH_PRESET:-largecaps}" in text
    assert "curl -fsS -X POST -m 3610" in text
