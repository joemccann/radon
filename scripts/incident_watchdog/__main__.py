"""CLI: python -m scripts.incident_watchdog [--once | --interval SECS]

--once          one probe/classify/record cycle (systemd timer mode; default)
--interval N    loop every N seconds (laptop/dev mode)
--max-cycles N  bound the loop (default 0 = until interrupted; --once implies 1)
--dir PATH      incident directory (default data/incidents; or
                INCIDENT_WATCHDOG_DIR)

Exit code 2 when any P1 incident is open after the cycle (lets a systemd
OnFailure= hook page without this script owning notification policy).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Same pattern as scripts/watchdog/__main__.py: systemd
# `python -m scripts.incident_watchdog --once` from the repo root does not
# put scripts/ on sys.path, so `from db.service_cycle` raises
# ModuleNotFoundError (page 05511a4f, 2026-08-28 16:05Z).
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from .classify import classify
from .probes import gather_findings
from .store import record_cycle


def run_cycle(directory: Path) -> dict:
    now = datetime.now(timezone.utc)
    findings = gather_findings()
    incidents = classify(findings, now)
    # An indeterminate probe is not evidence that its condition recovered,
    # but it must not latch incidents other probes definitively observed —
    # the store scopes resolution to each incident's own bearing probes.
    indeterminate = {
        name for name, finding in findings.items()
        if finding.get("state") == "unknown"
    }
    result = record_cycle(incidents, directory, now,
                          indeterminate_probes=indeterminate)
    summary = {
        "at": now.isoformat(),
        "probes": {name: finding.get("state") for name, finding in findings.items()},
        "incidents": [
            {"case_id": i["case_id"], "severity": i["severity"],
             "fingerprint": i["fingerprint"]}
            for i in incidents
        ],
        **result,
    }
    print(json.dumps(summary), flush=True)
    return summary


def has_open_p1(summary: dict) -> bool:
    return any(i["severity"] == "P1" for i in summary["incidents"])


SERVICE_NAME = "incident-watchdog"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="incident_watchdog")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--max-cycles", type=int, default=0)
    parser.add_argument(
        "--dir",
        default=os.environ.get("INCIDENT_WATCHDOG_DIR", "data/incidents"),
    )
    args = parser.parse_args(argv)

    directory = Path(args.dir)
    if args.once or args.interval is None:
        # R-325: the timer fires every 5 min around the clock and the job wrote
        # NO service_health row, so a wedged prober was in neither catalog and
        # nothing noticed it had stopped probing.
        from db.service_cycle import service_cycle  # noqa: PLC0415 — optional dep

        with service_cycle(SERVICE_NAME, market_hours_class="continuous"):
            summary = run_cycle(directory)
        # An OPEN P1 is a finding, not a watchdog failure: exiting 2 inside the
        # cycle would record the prober itself as errored every time it did its
        # job. The heartbeat closes `ok` above, then the finding sets the code.
        return 2 if has_open_p1(summary) else 0

    cycles = 0
    last = {"incidents": []}
    while True:
        last = run_cycle(directory)
        cycles += 1
        if args.max_cycles and cycles >= args.max_cycles:
            break
        time.sleep(args.interval)
    return 2 if has_open_p1(last) else 0


if __name__ == "__main__":
    sys.exit(main())
