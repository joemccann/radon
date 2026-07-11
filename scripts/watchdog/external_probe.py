"""On-box dead-man check for the independent GitHub edge probe."""
from __future__ import annotations

from datetime import datetime, timezone

from health_probe import reader
from health_service import turso_http

from .check import CheckOutcome


SERVICE = "external-health-probe"
FETCH_TIMEOUT_SECONDS = 2.5
EXPECTED_SOURCE = "github-actions/edge"


def check_external_probe(*, now: datetime | None = None) -> CheckOutcome:
    checked_at = now or datetime.now(timezone.utc)
    try:
        row = turso_http.fetch_external_probe(
            timeout=FETCH_TIMEOUT_SECONDS,
            source=EXPECTED_SOURCE,
        )
        verdict = reader.classify_external_probe(row, now=checked_at)
    except Exception as exc:  # malformed rows and DB failures must fail closed
        verdict = {"verdict": reader.VERDICT_STALE, "reason": f"probe_read_failed: {exc}"}

    state = verdict["verdict"]
    if state == reader.VERDICT_HEALTHY:
        return CheckOutcome(
            service=SERVICE,
            kind="deadman",
            status="healthy",
            severity=None,
            fired=False,
            message="off-box observer current",
            consecutive_failures=0,
            now=checked_at,
        )

    reason = str(verdict.get("reason") or state)
    if state == reader.VERDICT_STALE:
        age = verdict.get("age_seconds")
        age_text = f" for {int(float(age) // 60)}m" if age is not None else ""
        message = f"off-box observer silent{age_text} ({reason})"
        status = "stale"
    else:
        message = f"off-box observer reports edge unhealthy ({reason})"
        status = "error"

    # The reader already tolerates measured GitHub dispatch lag (2h window), so
    # another hysteresis layer would only delay a confirmed monitoring outage.
    return CheckOutcome(
        service=SERVICE,
        kind="deadman",
        status=status,
        severity="P1",
        fired=True,
        message=message,
        consecutive_failures=1,
        now=checked_at,
    )
