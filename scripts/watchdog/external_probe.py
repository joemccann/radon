"""On-box dead-man check for the independent GitHub edge probe."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
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

# Deploy artifacts written by cloud/scripts/deploy.sh on this host. The
# transition journal exists only while a deploy owns the app tier; the green
# marker's mtime records when the last deploy finished.
DEPLOY_GREEN_MARKER_FILE = os.environ.get(
    "RADON_DEPLOY_GREEN_MARKER", "/home/radon/.radon-last-green-deploy"
)
DEPLOY_TRANSITION_JOURNAL_FILE = os.environ.get(
    "RADON_DEPLOY_TRANSITION_JOURNAL", "/home/radon/.radon-deploy-transition.json"
)
DEPLOY_WINDOW_LOOKBACK_SECONDS = 900
DEPLOY_MARKER_GRACE_SECONDS = 60
# R-057: an interrupted deploy leaves the transition journal on disk forever
# (recover_pending_transition failed), a state that also blocks every later
# deploy, so it cannot self-clear. A journal older than this is STRANDED: it
# stops counting as deploy evidence (edge 5xx pages normally) and is raised as
# its own alarm. Sized like units.DEPLOY_COLLATERAL_WINDOW_SECS — ~4x the
# 900s deploy budget, covering stacked deploys.
TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS = 3600

_EDGE_5XX_REASON = re.compile(r"(?:ping|status)_http_5\d\d$")


def _read_local_aggregate(timeout: float = FETCH_TIMEOUT_SECONDS) -> dict | None:
    """Bounded on-box ``/status`` read. None on transport or parse failure."""
    request = urllib.request.Request(
        LOCAL_STATUS_URL,
        headers={"Accept": "application/json", "User-Agent": "radon-watchdog"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not 200 <= int(response.status) < 300:
                return None
            payload = json.loads(response.read(262_144).decode("utf-8"))
    except Exception:  # noqa: BLE001 - recovery must fail closed
        return None
    return payload if isinstance(payload, dict) else None


def _local_aggregate_is_healthy(timeout: float = FETCH_TIMEOUT_SECONDS) -> bool:
    """True only for schema-v2 ``up``. Used by deploy-window 5xx suppression."""
    payload = _read_local_aggregate(timeout)
    if payload is None:
        return False
    return (
        payload.get("schema_version") == 2
        and payload.get("ok") is True
        and str(payload.get("overall_state") or "").lower() == "up"
    )


def _local_aggregate_clears_offbox_down(timeout: float = FETCH_TIMEOUT_SECONDS) -> bool:
    """Confirm recovery from a validated off-box ``aggregate_down`` sample.

    The off-box row is authoritative for perimeter failures, but its irregular
    GitHub schedule can leave a recovered aggregate red for tens of minutes.
    The aggregate itself is produced on-box. Schema-v2 ``up`` (serving path
    green) and ``degraded`` (sidecar/broker only; ping already proved the edge
    is serving) are both recovery evidence. Never use this fallback for
    ping/status reachability failures: only an off-box observer can prove that
    the public perimeter recovered. ``starting``/``down``/``unknown`` stay
    fail-closed — those can be a serving-path restart.
    """
    payload = _read_local_aggregate(timeout)
    if payload is None or payload.get("schema_version") != 2:
        return False
    state = str(payload.get("overall_state") or "").lower()
    ok = payload.get("ok")
    if state == "up":
        return ok is True
    if state == "degraded":
        return ok is False
    return False


def _transition_journal_age_seconds(now: datetime) -> float | None:
    """Age of the deploy transition journal, or None when absent."""
    try:
        mtime = os.stat(DEPLOY_TRANSITION_JOURNAL_FILE).st_mtime
    except OSError:
        return None
    return (now - datetime.fromtimestamp(mtime, tz=timezone.utc)).total_seconds()


def _transition_journal_is_stranded(now: datetime) -> bool:
    age = _transition_journal_age_seconds(now)
    return age is not None and age > TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS


def _sampled_during_deploy_window(sample_time: datetime, now: datetime) -> bool:
    """True when the off-box sample overlaps a deploy's service restart.

    An in-flight deploy is evidenced by a FRESH transition journal; a
    just-finished one by the green marker's mtime. A stranded journal (an
    interrupted deploy) is not deploy evidence — no live deploy exists to
    blame, so nothing is suppressed. Both artifacts are radon-owned on the
    VPS; on hosts without them (laptop mode) this never suppresses.
    """
    lookback = timedelta(seconds=DEPLOY_WINDOW_LOOKBACK_SECONDS)
    journal_age = _transition_journal_age_seconds(now)
    if journal_age is not None:
        if journal_age > TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS:
            return False
        return now - sample_time <= lookback
    try:
        marker_mtime = os.stat(DEPLOY_GREEN_MARKER_FILE).st_mtime
    except OSError:
        return False
    marker_time = datetime.fromtimestamp(marker_mtime, tz=timezone.utc)
    grace = timedelta(seconds=DEPLOY_MARKER_GRACE_SECONDS)
    return marker_time - lookback <= sample_time <= marker_time + grace


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
        and _local_aggregate_clears_offbox_down()
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

    # A validated edge 5xx whose sample was taken while a deploy was cycling
    # the app tier is restart collateral, not an outage (2026-08-09: the
    # 17:43Z probe ran entirely inside the Deploy-to-VPS window and paged P1).
    # Scope is deliberately narrow: 5xx-through-Caddy reasons only — transport
    # failures (*_unreachable) can't be produced by a deploy, which never
    # stops Caddy — and the local aggregate must be healthy (fail closed).
    # The next off-box cycle still independently proves perimeter recovery.
    if state == reader.VERDICT_DOWN:
        down_reason = str(verdict.get("reason") or "")
        age = verdict.get("age_seconds")
        sample_time = checked_at - timedelta(seconds=float(age)) if age is not None else None
        if (
            _EDGE_5XX_REASON.fullmatch(down_reason)
            and sample_time is not None
            and _sampled_during_deploy_window(sample_time, checked_at)
            and _local_aggregate_is_healthy()
        ):
            return CheckOutcome(
                service=SERVICE,
                kind="deadman",
                status="healthy",
                severity=None,
                fired=False,
                message=f"off-box {down_reason} sampled inside deploy window; local aggregate healthy",
                consecutive_failures=0,
                now=checked_at,
            )

    if state == reader.VERDICT_HEALTHY:
        # A stranded journal blocks every subsequent deploy and cannot
        # self-clear — alarm-worthy on its own, not only when it happens
        # to coincide with an edge failure (R-057).
        journal_age = _transition_journal_age_seconds(checked_at)
        if journal_age is not None and journal_age > TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS:
            return CheckOutcome(
                service=SERVICE,
                kind="deadman",
                status="error",
                severity="P2",
                fired=True,
                message=(
                    f"deploy transition journal stranded for {int(journal_age // 60)}m "
                    f"({DEPLOY_TRANSITION_JOURNAL_FILE}) — an interrupted deploy left it "
                    "behind; deploys are blocked until it is recovered/removed"
                ),
                consecutive_failures=1,
                now=checked_at,
            )
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
