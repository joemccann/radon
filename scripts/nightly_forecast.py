#!/usr/bin/env python3
"""Thin CLI for the nightly forecasting runner.

Orchestrates backfill -> flow-surprise -> calibration and prints the summary
as JSON on stdout. See ``forecasting.nightly_forecast`` for the logic and
``docs/forecasting-deploy.md`` for the systemd timer that drives it.

Usage:
    python3 scripts/nightly_forecast.py --metric flow_strength --top 25 \
        --lookback 250 --backfill-days 20
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forecasting.nightly_forecast import main

if __name__ == "__main__":
    sys.exit(main())
