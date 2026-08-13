"""Scheduled LEAP/GARCH refresh wrappers default to the indexes universe."""
from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent


def test_leap_refresh_defaults_to_indexes_and_3610s_curl():
    text = (SCRIPTS / "run_leap_refresh.sh").read_text()
    assert "RADON_LEAP_REFRESH_PRESET:-indexes}" in text
    assert "curl -fsS -X POST -m 3610" in text


def test_garch_refresh_defaults_to_indexes_and_3610s_curl():
    text = (SCRIPTS / "run_garch_refresh.sh").read_text()
    assert "RADON_GARCH_REFRESH_PRESET:-indexes}" in text
    assert "curl -fsS -X POST -m 3610" in text
