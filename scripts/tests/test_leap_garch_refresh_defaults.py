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
    # R-144 replaced `curl -fsS` with an exit-code + status read so a 502 or
    # a timeout can be told apart from connection-refused. The 3610s default
    # is unchanged; it now lives in a named variable.
    assert "RADON_SCAN_FASTAPI_TIMEOUT_SECS:-3610}" in text
    assert '-m "$FASTAPI_TIMEOUT_SECS"' in text
    # 2026-08-27 capacity shed: wait budget fits under TimeoutStartSec=3900
    # with the 3610s scan curl still intact.
    assert "RADON_LEAP_SHED_WAIT_SECS:-240}" in text
    assert "subprocess capacity exhausted" in text


def test_garch_refresh_defaults_to_largecaps_and_3610s_curl():
    text = (SCRIPTS / "run_garch_refresh.sh").read_text()
    assert "RADON_GARCH_REFRESH_PRESET:-largecaps}" in text
    assert "RADON_SCAN_FASTAPI_TIMEOUT_SECS:-3610}" in text
    assert '-m "$FASTAPI_TIMEOUT_SECS"' in text
