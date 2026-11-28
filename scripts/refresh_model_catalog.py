#!/usr/bin/env python3
"""Model-catalog job: native discovery lives in the gateway; app persistence stays here.

Frontier selection, the provider set, and the Turso/JSON store are
``model_gateway._catalog_backend``. This process scrubs model grants, asks
the gateway to refresh, and writes the reply with that store.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from model_gateway._catalog_backend import (  # noqa: E402
    _record_cycle_failure,
    load_previous,
    persist_result,
)
from model_gateway.client import request as gateway_request  # noqa: E402
from model_gateway.client import scrub_model_secrets  # noqa: E402

from utils.ipv4_first import prefer_ipv4  # noqa: E402

prefer_ipv4()


def _log(message: str) -> None:
    print(f"[model-catalog] {message}", file=sys.stderr)


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def run(scan_time: Optional[str] = None) -> dict:
    scrub_model_secrets()
    stamp = scan_time or _iso_utc_now()
    reply = gateway_request(
        "refresh-catalog",
        {"scan_time": stamp, "previous": load_previous()},
        timeout_seconds=240,
    )
    payload, rows, failed = reply["catalog"], reply["rows"], reply["failed"]
    persist_result(payload, rows, failed)
    return payload


def _print_summary(payload: dict) -> None:
    print(f"\nModel catalog - {len(payload['models'])} provider(s)", file=sys.stderr)
    for model in payload["models"]:
        marker = "*" if model["model_id"] == payload["defaultId"] else " "
        print(f"  {marker} {model['provider']:<10} {model['model_id']}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh the chat model picker catalog (one frontier model per keyed provider)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args(argv)

    scan_time = _iso_utc_now()
    try:
        payload = run(scan_time)
    except Exception as exc:  # noqa: BLE001 - R-455/R-458: a crash must leave a row, not a traceback
        _log(f"cycle failed: {exc}")
        _record_cycle_failure(scan_time, exc)
        return 1
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
