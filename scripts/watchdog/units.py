"""Systemd unit flap / failure watchdog — ALERT-ONLY.

Rides the continuous bucket (radon-watchdog-continuous.timer, every
5 min, 24/7) via ``__main__._cmd_bucket``. The DUR-02 StartLimit brake
(radon-cloud unit files: StartLimitIntervalSec=300, StartLimitBurst=5)
parks a crash-looping unit as ``failed`` with ``Result=start-limit-hit``
instead of restarting it forever — but a parked unit NEVER auto-recovers,
so the brake without this alarm converts an infinite flap into a
permanent silent outage. Context: radon-beta-nextjs restarted 160,951
times over 10 days before anything noticed.

Three signals, one alert max per unit per cycle (highest severity wins):

  * ``failed``   — ActiveState=failed. P1, except Type=oneshot
    ``Result=exit-code`` re-observed at the same InactiveEnterTimestamp
    (P3: the next timer is the retry). ``Result=start-limit-hit`` is
    called out explicitly because it requires a manual operator action.
  * ``flap``     — SubState=auto-restart observed in two consecutive
    watchdog cycles. P1 (sustained crash loop). **Unreachable for any
    DUR-02-braked unit**: ``auto-restart`` is occupied only for
    ``RestartSec`` per restart (5s api/nextjs, 10s monitor, 2s health), and
    the brake parks the unit ``failed`` in ``RestartSec x StartLimitBurst``
    — roughly 25s — so two five-minute samples cannot both land inside a
    five-second window. Real coverage for that shape is the
    ``Result=start-limit-hit`` branch below, which does page P1. The only
    unit ``flap`` can catch is an UNBRAKED ``radon-*`` unit looping with a
    long ``RestartSec``; kept for that case rather than removed, but it is
    not a second independent P1 signal. R-268.
  * ``delta``    — NRestarts increased since the last cycle. P3 early
    signal; NRestarts only counts systemd-initiated Restart= restarts,
    never manual/deploy ``systemctl restart`` (those reset it to 0).

Last-cycle state persists to ``data/watchdog_units_state.json`` for the
flap / delta comparisons.

ALERT-ONLY contract: this module reads ``systemctl show`` and nothing
else — it must never start/stop/restart a unit
(feedback_ib_auto_recovery_conservative). Dispatch (cooldown gate,
Pushover for P1, journalctl logging) is handled by the continuous
bucket's existing ``grouping.dispatch_with_grouping`` path; like the
sibling continuous checks, no per-check ``service_health`` row is
written — the ``watchdog-alerts`` dispatcher row covers this writer.

Hosts without systemctl (the laptop) degrade to a no-op.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .check import CheckOutcome

log = logging.getLogger("watchdog.units")


UNIT_GLOB = "radon-*"
# ExecMainStatus distinguishes a graceful SIGTERM unwind (143 = 128+15)
# recorded as Result=exit-code from a real application exit-code failure.
# bpi_scan (and siblings) catch SIGTERM, raise SystemExit(143), and systemd
# then reports exit-code rather than signal — same stop-clean collateral.
SHOW_PROPERTIES = (
    "Id,ActiveState,SubState,Result,NRestarts,InactiveEnterTimestamp,"
    "Type,ExecMainStatus"
)
SYSTEMCTL_TIMEOUT_S = 10
# Process handled SIGTERM and exited 128+SIGTERM. systemd Result=exit-code.
GRACEFUL_SIGTERM_EXIT_STATUS = 143

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE_PATH = _PROJECT_DIR / "data" / "watchdog_units_state.json"

# Deploy evidence for the signal-kill downgrade: deploy.sh's stop-clean
# SIGTERMs in-flight oneshots (radon-bpi 2026-08-05 21:40:24Z, killed the
# same second as radon-deploy-root stop-clean). The transition journal
# exists only mid-deploy; the green marker's mtime stamps deploy end.
# 2026-08-14 stacked deploys: stop-clean of deploy N at 22:52:36Z,
# green of deploy N+2 at 23:27:11Z (34 min). A 20-min single-deploy
# budget paged that as P1. Window covers ~4x DEPLOY_TIMEOUT=900.
GREEN_MARKER_PATH = Path("/home/radon/.radon-last-green-deploy")
TRANSITION_JOURNAL_PATH = Path("/home/radon/.radon-deploy-transition.json")
DEPLOY_COLLATERAL_WINDOW_SECS = 60 * 60
# R-064: kill-before-green compares two FROZEN timestamps (kill vs marker), so
# without a now-cap a unit that never runs again stays P3-digest forever. The
# cap must exceed the longest legit recover-on-next-timer horizon: a daily
# oneshot's next fire is at most ~24h after the kill (2026-08-15 radon-bpi:
# a 60-min cap here false-paged that exact shape). Past 24h the unit is
# frozen — the deploy left it dead (e.g. timer disabled) — and it re-pages P1.
KILL_BEFORE_GREEN_FROZEN_CAP_SECS = 24 * 60 * 60

# R-157: the `in_flight` branch keyed on nothing but the journal EXISTING.
# R-057 established that an interrupted deploy leaves it on disk indefinitely
# and cannot self-clear, and shipped this same bound in external_probe.py —
# units.py never got it. While a stranded journal sits there (a state the
# fleet already alarms at P2), every OOM-kill, `systemctl kill` or
# stop-timeout SIGKILL of any radon-* unit in the previous 24h was
# reclassified P3 digest instead of P1 page. Kept numerically identical to
# external_probe's, and pinned by a test.
TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS = 3600


# ── systemctl seam ───────────────────────────────────────────────────

def _run_systemctl_show() -> str:
    """Read-only probe. Verified invocation on the VPS 2026-06-12:
    ``systemctl show 'radon-*' -p Id,...`` expands the glob over loaded
    units and prints one property block per unit, blank-line separated.
    """
    result = subprocess.run(
        ["systemctl", "show", UNIT_GLOB, "-p", SHOW_PROPERTIES, "--no-pager"],
        capture_output=True,
        text=True,
        timeout=SYSTEMCTL_TIMEOUT_S,
        check=True,
    )
    return result.stdout


# ── parsing ──────────────────────────────────────────────────────────

def parse_show_output(text: str) -> list[dict]:
    """Blank-line-separated ``Key=Value`` blocks → list of unit dicts.

    ``NRestarts`` is an int when systemd reports it (services) and
    ``None`` when it doesn't (timers). Blocks without an ``Id`` are
    dropped — they can't be attributed to a unit.
    """
    parsed = []
    for block in text.split("\n\n"):
        props: dict = {}
        for line in block.splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            props[key] = value
        if not props.get("Id"):
            continue
        nrestarts = props.get("NRestarts")
        props["NRestarts"] = int(nrestarts) if nrestarts is not None and nrestarts.isdigit() else None
        exec_status = props.get("ExecMainStatus")
        props["ExecMainStatus"] = (
            int(exec_status) if exec_status is not None and exec_status.isdigit() else None
        )
        parsed.append(props)
    return parsed


def _is_graceful_sigterm_exit(unit: dict) -> bool:
    """True when a oneshot caught SIGTERM and exited 143.

    Default SIGTERM disposition yields Result=signal. Handlers that unwind
    via SystemExit(143) (bpi_scan.install_sigterm_unwind) make systemd
    record Result=exit-code instead — still stop-clean collateral when
    the kill sits inside a deploy window (2026-09-01 page e741ed1a).
    """
    if (unit.get("Result") or "") != "exit-code":
        return False
    return unit.get("ExecMainStatus") == GRACEFUL_SIGTERM_EXIT_STATUS


# ── deploy-collateral evidence ───────────────────────────────────────

def _parse_systemd_timestamp(value: str) -> Optional[datetime]:
    """``systemctl show`` timestamp ("Wed 2026-08-05 21:40:24 UTC") →
    aware datetime; None for empty / "n/a"."""
    from datetime import timezone as _tz

    if not value or value == "n/a":
        return None
    try:
        parsed = datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S %Z")
    except ValueError:
        return None
    return parsed.replace(tzinfo=_tz.utc)


def _read_deploy_evidence() -> dict:
    from datetime import timezone as _tz

    marker_mtime = None
    try:
        if GREEN_MARKER_PATH.is_file():
            marker_mtime = datetime.fromtimestamp(
                GREEN_MARKER_PATH.stat().st_mtime, tz=_tz.utc
            )
    except OSError:
        pass
    journal_age_seconds: Optional[float] = None
    try:
        journal_age_seconds = (
            datetime.now(_tz.utc)
            - datetime.fromtimestamp(TRANSITION_JOURNAL_PATH.stat().st_mtime, tz=_tz.utc)
        ).total_seconds()
    except OSError:
        journal_age_seconds = None
    return {
        "marker_mtime": marker_mtime,
        "in_flight": TRANSITION_JOURNAL_PATH.exists(),
        "journal_age_seconds": journal_age_seconds,
    }


def _journal_is_stranded(deploy: dict) -> bool:
    """True when the transition journal is too old to be evidence of a deploy.

    An unknown age is treated as stranded: the journal cannot self-clear, so
    "we could not read its mtime" is not a reason to keep excusing kills.
    """
    age = deploy.get("journal_age_seconds")
    if age is None:
        return True
    return age > TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS


def _is_deploy_collateral(unit: dict, deploy: Optional[dict], now: datetime) -> bool:
    """True when a stop-clean SIGTERM failure sits inside a deploy window —
    killing an in-flight oneshot, not an outage.

    Matches ``Result=signal`` (default SIGTERM disposition) and
    ``Result=exit-code`` with ``ExecMainStatus=143`` (process caught
    SIGTERM and exited 128+15; 2026-09-01 radon-bpi page e741ed1a).

    Covers three stacked-deploy shapes:
      * kill before the latest green (completed stop-clean; checked first)
      * journal present (in_flight)
      * kill after the last green (cancelled / not-yet-green successor)
    Kill-before-green is evaluated before ``in_flight``: a successor
    deploy's fresh transition journal must not re-page a latched oneshot
    already explained by an earlier greened stop-clean (2026-08-25
    16:20Z a70a393e: BPI kill 12:41, green 15:14, successor journal
    16:19 → in_flight 60-min age cap false-paged P1). The 60-min
    now-to-kill cap still applies to in_flight and kill-after-green so
    an unrelated SIGTERM hours later pages (T-103). Kill-before-green
    uses the 24h oneshot recovery horizon
    (``KILL_BEFORE_GREEN_FROZEN_CAP_SECS``): Type=oneshot stays failed
    until its next timer (2026-08-15 01:35Z radon-bpi), and a stacked
    successor can overwrite the green marker hours after the first
    stop-clean (2026-08-20 02:45Z radon-bpi: kill 00:04, a231 green
    00:05, 0f7d green 02:42 → kill-to-latest-marker 158 min).
    """
    if not deploy:
        return False
    result = unit.get("Result") or ""
    if result != "signal" and not _is_graceful_sigterm_exit(unit):
        return False
    failed_at = _parse_systemd_timestamp(unit.get("InactiveEnterTimestamp") or "")
    if failed_at is None:
        return False
    age = (now - failed_at).total_seconds()
    if age < 0:
        return False
    marker = deploy.get("marker_mtime")
    if marker is not None and failed_at < marker:
        if age > KILL_BEFORE_GREEN_FROZEN_CAP_SECS:
            return False
        return 0 <= (marker - failed_at).total_seconds() <= KILL_BEFORE_GREEN_FROZEN_CAP_SECS
    if deploy.get("in_flight") and not _journal_is_stranded(deploy):
        return age <= DEPLOY_COLLATERAL_WINDOW_SECS
    if marker is None:
        return False
    return age <= DEPLOY_COLLATERAL_WINDOW_SECS


# ── state persistence ────────────────────────────────────────────────

def _load_state(path: Path) -> dict:
    """Previous-cycle snapshot keyed by unit id; {} when missing or
    corrupt (a corrupt file just costs one cycle of flap/delta memory).
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    units = payload.get("units")
    return units if isinstance(units, dict) else {}


def _save_state(path: Path, current: list[dict], now: datetime) -> None:
    snapshot = {
        unit["Id"]: {
            "nrestarts": unit.get("NRestarts"),
            "auto_restart": unit.get("SubState") == "auto-restart",
            "active_state": unit.get("ActiveState"),
            "inactive_enter": unit.get("InactiveEnterTimestamp"),
            "unit_type": unit.get("Type"),
        }
        for unit in current
    }
    payload = {"cycle_at": now.isoformat().replace("+00:00", "Z"), "units": snapshot}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# ── evaluation ───────────────────────────────────────────────────────

def _outcome_for(*, unit_id: str, severity: str, message: str, now: datetime) -> CheckOutcome:
    return CheckOutcome(
        service=unit_id,
        kind="unit",
        status="error",
        severity=severity,
        fired=True,
        message=message,
        consecutive_failures=1,
        now=now,
    )


def _is_oneshot_exit_code_latch(unit: dict, previous: dict) -> bool:
    """True when this is the same completed oneshot ExecStart still sitting
    in ActiveState=failed.

    Type=oneshot has no Restart=, so NRestarts stays 0 and systemd leaves
    the unit failed until the next timer fire. The first sight of a given
    InactiveEnterTimestamp is a real ExecStart failure and still pages P1.
    Re-observing the same timestamp is the latch, not a new outage
    (2026-08-24 radon-flow-refresh capacity shed).
    """
    if unit.get("Type") != "oneshot":
        return False
    if (unit.get("Result") or "") != "exit-code":
        return False
    ts = unit.get("InactiveEnterTimestamp") or ""
    if not ts or ts == "n/a":
        return False
    prior = previous.get(unit.get("Id") or "") or {}
    return (prior.get("inactive_enter") or "") == ts


def _failed_alert(
    unit: dict,
    now: datetime,
    deploy: Optional[dict] = None,
    previous: Optional[dict] = None,
) -> Optional[CheckOutcome]:
    if unit.get("ActiveState") != "failed":
        return None
    result = unit.get("Result") or "unknown"
    if _is_deploy_collateral(unit, deploy, now):
        # stop-clean SIGTERMed an in-flight oneshot mid-deploy. The unit
        # recovers on its next timer fire; digest-tier note, never a page
        # (feedback_deploy_stop_clean_fails_inflight_scan_oneshots).
        message = (
            f"signal-killed inside a deploy window (Result={result}) — "
            "deploy stop-clean collateral, recovers on the next timer fire"
        )
        return _outcome_for(unit_id=unit["Id"], severity="P3", message=message, now=now)
    if _is_oneshot_exit_code_latch(unit, previous or {}):
        message = (
            f"oneshot Result=exit-code latched "
            f"(NRestarts={unit.get('NRestarts')}) — next timer retries"
        )
        return _outcome_for(unit_id=unit["Id"], severity="P3", message=message, now=now)
    message = f"systemd unit failed (Result={result}, NRestarts={unit.get('NRestarts')})"
    if result == "start-limit-hit":
        message += (
            " — start limit hit: systemd parked the unit and it will NOT "
            "auto-recover; run systemctl reset-failed + start"
        )
    return _outcome_for(unit_id=unit["Id"], severity="P1", message=message, now=now)


def _flap_alert(unit: dict, previous: dict, now: datetime) -> Optional[CheckOutcome]:
    if unit.get("SubState") != "auto-restart":
        return None
    prior = previous.get(unit["Id"]) or {}
    if not prior.get("auto_restart"):
        return None
    message = (
        f"crash-looping: auto-restart in 2 consecutive watchdog cycles "
        f"(NRestarts={unit.get('NRestarts')}, Result={unit.get('Result')})"
    )
    return _outcome_for(unit_id=unit["Id"], severity="P1", message=message, now=now)


def _delta_alert(unit: dict, previous: dict, now: datetime) -> Optional[CheckOutcome]:
    prior = previous.get(unit["Id"]) or {}
    prev_n, curr_n = prior.get("nrestarts"), unit.get("NRestarts")
    if not isinstance(prev_n, int) or not isinstance(curr_n, int):
        return None
    delta = curr_n - prev_n
    if delta <= 0:
        # Equal → steady; negative → counter reset by a manual/deploy
        # restart, not a crash.
        return None
    message = f"NRestarts +{delta} since last cycle ({prev_n}→{curr_n})"
    return _outcome_for(unit_id=unit["Id"], severity="P3", message=message, now=now)


def _recovery_observation(
    unit: dict, previous: dict, now: datetime
) -> Optional[CheckOutcome]:
    """Emit health when a prior P1-capable unit condition has cleared."""
    prior = previous.get(unit["Id"]) or {}
    was_p1_condition = (
        prior.get("active_state") == "failed" or prior.get("auto_restart") is True
    )
    is_p1_condition = (
        unit.get("ActiveState") == "failed" or unit.get("SubState") == "auto-restart"
    )
    if not was_p1_condition or is_p1_condition:
        return None
    return CheckOutcome(
        service=unit["Id"],
        kind="unit",
        status="healthy",
        severity=None,
        fired=False,
        message="systemd unit recovered",
        consecutive_failures=0,
        now=now,
    )


def evaluate(
    *,
    current: list[dict],
    previous: dict,
    now: datetime,
    deploy: Optional[dict] = None,
) -> list[CheckOutcome]:
    """One alert max per unit, plus a prior-P1 recovery observation."""
    outcomes = []
    for unit in current:
        p1_alert = _failed_alert(unit, now, deploy, previous) or _flap_alert(
            unit, previous, now
        )
        recovery = None if p1_alert else _recovery_observation(unit, previous, now)
        if recovery:
            outcomes.append(recovery)
        alert = p1_alert or _delta_alert(unit, previous, now)
        if alert:
            outcomes.append(alert)
    return outcomes


# ── public entry point ───────────────────────────────────────────────

def check_units(
    *,
    now: datetime,
    state_path: Path = DEFAULT_STATE_PATH,
    show_runner: Callable[[], str] = _run_systemctl_show,
) -> list[CheckOutcome]:
    """Probe systemd, compare against the persisted last cycle, persist
    the new snapshot, and return fired outcomes plus prior-P1 recovery observations.

    Best-effort by design: any probe failure degrades to [] with a
    warning — a broken units check must never abort the bucket cycle
    that also covers the service_health staleness checks.
    """
    probing_real_systemd = show_runner is _run_systemctl_show
    if probing_real_systemd and shutil.which("systemctl") is None:
        log.info("systemctl not available on this host — skipping units check")
        return []

    try:
        current = parse_show_output(show_runner())
    except Exception as exc:  # noqa: BLE001 — degrade, never kill the bucket
        log.warning("units check probe failed: %s", exc)
        return []
    if not current:
        return []

    previous = _load_state(state_path)
    outcomes = evaluate(
        current=current, previous=previous, now=now, deploy=_read_deploy_evidence()
    )
    try:
        _save_state(state_path, current, now)
    except OSError as exc:
        log.warning("units state write failed (%s): %s", state_path, exc)
    return outcomes
