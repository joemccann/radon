"""On-box dead-man check for the independent GitHub edge probe."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import urllib.request

from health_probe import reader
from health_service import turso_http

from .check import CheckOutcome


SERVICE = "external-health-probe"
FETCH_TIMEOUT_SECONDS = 2.5
EXPECTED_SOURCE = "github-actions/edge"
LOCAL_STATUS_URL = "http://127.0.0.1:8330/status"
GITHUB_RUNS_URL = (
    "https://api.github.com/repos/joemccann/radon/actions/workflows/"
    "external-health-probe.yml/runs?per_page=1"
)


def _local_aggregate_is_healthy(timeout: float = FETCH_TIMEOUT_SECONDS) -> bool:
    """Confirm recovery from a validated off-box ``aggregate_down`` sample.

    The off-box row is authoritative for perimeter failures, but its irregular
    GitHub schedule can leave a recovered aggregate red for tens of minutes.
    The aggregate itself is produced on-box, so a fresh schema-v2 healthy
    response is sufficient recovery evidence for this one failure class. Never
    use this fallback for ping/status reachability failures: only an off-box
    observer can prove that the public perimeter recovered.
    """
    request = urllib.request.Request(
        LOCAL_STATUS_URL,
        headers={"Accept": "application/json", "User-Agent": "radon-watchdog"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not 200 <= int(response.status) < 300:
                return False
            payload = json.loads(response.read(262_144).decode("utf-8"))
    except Exception:  # noqa: BLE001 - recovery must fail closed
        return False
    return (
        isinstance(payload, dict)
        and payload.get("schema_version") == 2
        and payload.get("ok") is True
        and str(payload.get("overall_state") or "").lower() == "up"
    )


def _latest_github_run(timeout: float = FETCH_TIMEOUT_SECONDS) -> dict | None:
    """Independent witness when Turso persistence is stale or unavailable."""
    request = urllib.request.Request(
        GITHUB_RUNS_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "radon-watchdog"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(262_144).decode("utf-8"))
        runs = payload.get("workflow_runs") or []
        return runs[0] if runs else None
    except Exception:  # noqa: BLE001 - preserve the primary Turso verdict
        return None


def _github_run_is_current_and_green(run: dict | None, now: datetime) -> bool:
    if not run or run.get("status") != "completed" or run.get("conclusion") != "success":
        return False
    updated = str(run.get("updated_at") or "")
    try:
        timestamp = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except ValueError:
        return False
    return 0 <= (now - timestamp).total_seconds() <= reader.STALE_AFTER_SECONDS


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

    if verdict["verdict"] == reader.VERDICT_STALE:
        github_run = _latest_github_run()
        if _github_run_is_current_and_green(github_run, checked_at):
            verdict = {"verdict": reader.VERDICT_HEALTHY, "reason": "github_workflow_current"}

    state = verdict["verdict"]
    if (
        state == reader.VERDICT_DOWN
        and verdict.get("reason") == "aggregate_down"
        and _local_aggregate_is_healthy()
    ):
        return CheckOutcome(
            service=SERVICE,
            kind="deadman",
            status="healthy",
            severity=None,
            fired=False,
            message="off-box aggregate recovered locally",
            consecutive_failures=0,
            now=checked_at,
        )

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
        # A silent observer is a monitoring gap, not a validated edge outage:
        # P2 digest. Only a validated off-box DOWN verdict earns the P1
        # emergency (2026-07-28 storm: hardcoded P1 here drove hours of
        # retry-until-ack pages for a condition downstream of the gateway).
        severity = "P2"
    else:
        message = f"off-box observer reports edge unhealthy ({reason})"
        status = "error"
        severity = "P1"

    # The reader already tolerates measured GitHub dispatch lag (2h window), so
    # another hysteresis layer would only delay a confirmed monitoring outage.
    return CheckOutcome(
        service=SERVICE,
        kind="deadman",
        status=status,
        severity=severity,
        fired=True,
        message=message,
        consecutive_failures=1,
        now=checked_at,
    )
