#!/usr/bin/env python3.13
"""Managed UW research collector. Disabled by default until capability admission."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading

from uw_stream.config import Config, hydrate_settings
from uw_stream.collector import run
from uw_stream.store import read_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", action="store_true", help="Read current research snapshot without opening a socket")
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(read_snapshot(), allow_nan=False))
        return 0
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        hydrate_settings()
        run(Config.from_env(), token=os.environ.get("UW_TOKEN"), stop=stop)
        return 0
    except (ValueError, RuntimeError, OSError):
        logging.error("UW research collector configuration or ownership unavailable")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
