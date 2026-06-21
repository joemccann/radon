"""Calibration report CLI — repeatable backtest harness over the watchlist.

Thin scripts/-root entry mirroring scripts/forecast_backtest.py so production
has a stable invocation path. All logic lives in
forecasting.calibration_report.

Offline analysis tool: NO service_cycle. stdout is reserved for the result
JSON; progress goes to stderr.

Usage:
    python3 scripts/forecast_calibration.py --metric flow_strength \
        --lookback 250 --horizon 1 --persist
    python3 scripts/forecast_calibration.py --ticker AAPL
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from forecasting import calibration_report


if __name__ == "__main__":
    sys.exit(calibration_report.main())
