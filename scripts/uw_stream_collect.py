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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--snapshot", action="store_true", help="Read current research snapshot without opening a socket")
    mode.add_argument("--replay", action="store_true", help="Offline reconstruction from retained local ingest IDs; collector must be stopped")
    parser.add_argument("--replay-directory", help="Explicit retained-journal directory for offline replay")
    parser.add_argument("--from-ingest-id", type=int, default=1)
    parser.add_argument("--to-ingest-id", type=int)
    parser.add_argument("--max-events", type=int, default=10000)
    args = parser.parse_args()
    if args.replay:
        from uw_stream.replay import replay_retained
        try:
            result = replay_retained(args.replay_directory, from_ingest_id=args.from_ingest_id,
                                     to_ingest_id=args.to_ingest_id, max_events=args.max_events)
        except ValueError:
            print(json.dumps({"status": "unavailable", "error_code": "offline_replay_unavailable", "guidance": "Stop the collector and choose a retained bounded ingest-ID range"}))
            return 1
        print(json.dumps(result, allow_nan=False))
        return 0
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
