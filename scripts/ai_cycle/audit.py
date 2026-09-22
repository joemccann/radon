"""Read-only coverage audit; optionally copy evidence to an isolated SQLite store.

python -m scripts.ai_cycle.audit --copy-to /tmp/ai-audit.sqlite
No provider calls, production mutations, or credentials in the report.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .snapshot import build_snapshot, compact_snapshot, history_series, latest_vintages
from .store import ObservationStore


def audit_rows(rows):
    groups = defaultdict(list)
    for row in latest_vintages(rows):
        groups[(row["indicator_id"], row["source_id"], history_series(row), row["unit"])].append(row)
    result = []
    for (indicator, source, series, unit), values in sorted(groups.items()):
        dates = sorted({row["period_end"] for row in values})
        missing = []
        if indicator in ("D1", "D3", "P1"):
            observed = {value[:10] for value in dates}
            first, last = date.fromisoformat(dates[0][:10]), date.fromisoformat(dates[-1][:10])
            missing = [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)
                       if (first + timedelta(days=i)).isoformat() not in observed]
        result.append(dict(indicator_id=indicator, source_id=source, series_id=series, unit=unit,
            observation_count=len(values), date_count=len(dates), observed_from=dates[0], observed_through=dates[-1],
            distinct_values=len({row["value"] for row in values}), minimum=min(row["value"] for row in values),
            maximum=max(row["value"] for row in values), raw_hash_count=len({row["raw_hash"] for row in values}),
            incomplete_count=sum(row["metadata"].get("complete") is False for row in values), missing_dates=missing))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", help="Read isolated SQLite instead of production")
    parser.add_argument("--copy-to", help="New isolated SQLite evidence copy; existing files rejected")
    args = parser.parse_args()
    if args.copy_to and Path(args.copy_to).exists():
        parser.error("Evidence copy must be a new path")
    source = ObservationStore(args.database)
    rows = source.read_snapshot_observations()
    statuses = source.read_source_statuses()
    if args.copy_to:
        target = ObservationStore(args.copy_to)
        target.append_observations(rows)
        for status in statuses:
            target.record_source_status(status)
        target.write_api_snapshot(compact_snapshot(build_snapshot(target)))
        target.close()
    print(json.dumps(dict(series=audit_rows(rows), source_statuses=statuses, stored_vintages=len(rows)), indent=2))


if __name__ == "__main__":
    main()
