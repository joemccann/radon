"""Root-filesystem usage alarm — ALERT-ONLY, DB-free (R-069).

Rides the continuous bucket (every 5 min, 24/7) next to the units alarm
and the external-probe dead-man. Nothing else on the box watches the disk:
host_metrics_sampler records ``disk_pct`` for RCA, but a metric row nobody
reads cannot page — and a full root fs takes down every writer at once
(Turso fallback JSONLs, journal WALs, the deploy itself). Because the
check needs no DB it also runs on the snapshot-unavailable path, where a
full disk is a plausible cause of the outage being handled.

Dispatch (cooldown gate, Pushover, digest) is handled by the continuous
bucket's existing ``grouping.dispatch_with_grouping`` path, like the
sibling DB-free checks.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone

from .check import CheckOutcome

SERVICE = "root-disk-usage"
DISK_USAGE_PATH = "/"
# P2 leaves headroom to act before writers start failing; P1 means writes
# on a ~38 GB root fs are within a day or two of ENOSPC at normal growth.
DISK_P2_PCT = 85.0
DISK_P1_PCT = 95.0


def _healthy(message: str, now: datetime) -> CheckOutcome:
    return CheckOutcome(
        service=SERVICE,
        kind="disk",
        status="healthy",
        severity=None,
        fired=False,
        message=message,
        consecutive_failures=0,
        now=now,
    )


def check_disk(*, now: datetime | None = None, path: str = DISK_USAGE_PATH) -> CheckOutcome:
    checked_at = now or datetime.now(timezone.utc)
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        # Unreadable usage is not evidence of a full disk — never page on it.
        return _healthy(f"disk usage unreadable: {exc}", checked_at)
    if usage.total <= 0:
        return _healthy("disk usage unreadable: zero total", checked_at)

    pct = 100.0 * usage.used / usage.total
    free_gib = usage.free / (1024**3)
    if pct < DISK_P2_PCT:
        return _healthy(f"root fs {pct:.0f}% used ({free_gib:.1f} GiB free)", checked_at)

    severity = "P1" if pct >= DISK_P1_PCT else "P2"
    return CheckOutcome(
        service=SERVICE,
        kind="disk",
        status="error",
        severity=severity,
        fired=True,
        message=(
            f"root fs {pct:.0f}% used ({free_gib:.1f} GiB free) on {path} — "
            "writers fail at ENOSPC; prune caches/logs before it wedges the box"
        ),
        consecutive_failures=1,
        now=checked_at,
    )
