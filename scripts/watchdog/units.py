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

  * ``failed``   — ActiveState=failed. P1. ``Result=start-limit-hit`` is
    called out explicitly because it requires a manual operator action.
  * ``flap``     — SubState=auto-restart observed in two consecutive
    watchdog cycles. P1 (sustained crash loop).
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
SHOW_PROPERTIES = "Id,ActiveState,SubState,Result,NRestarts,InactiveEnterTimestamp"
SYSTEMCTL_TIMEOUT_S = 10

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE_PATH = _PROJECT_DIR / "data" / "watchdog_units_state.json"

# Deploy evidence for the signal-kill downgrade: deploy.sh's stop-clean
# SIGTERMs in-flight oneshots (radon-bpi 2026-08-05 21:40:24Z, killed the
# same second as radon-deploy-root stop-clean). The transition journal
# exists only mid-deploy; the green marker's mtime stamps deploy end.
GREEN_MARKER_PATH = Path("/home/radon/.radon-last-green-deploy")
TRANSITION_JOURNAL_PATH = Path("/home/radon/.radon-deploy-transition.json")
DEPLOY_COLLATERAL_WINDOW_SECS = 20 * 60  # covers the 15-min deploy budget


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
        parsed.append(props)
    return parsed


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
    marker_mtime = None
    try:
        if GREEN_MARKER_PATH.is_file():
            from datetime import timezone as _tz

            marker_mtime = datetime.fromtimestamp(
                GREEN_MARKER_PATH.stat().st_mtime, tz=_tz.utc
            )
    except OSError:
        pass
    return {
        "marker_mtime": marker_mtime,
        "in_flight": TRANSITION_JOURNAL_PATH.exists(),
    }


def _is_deploy_collateral(unit: dict, deploy: Optional[dict], now: datetime) -> bool:
    """True when a Result=signal failure sits inside a deploy window —
    stop-clean killing an in-flight oneshot, not an outage."""
    if not deploy or unit.get("Result") != "signal":
        return False
    failed_at = _parse_systemd_timestamp(unit.get("InactiveEnterTimestamp") or "")
    if failed_at is None:
        return False
    if deploy.get("in_flight"):
        if 0 <= (now - failed_at).total_seconds() <= DEPLOY_COLLATERAL_WINDOW_SECS:
            return True
    marker = deploy.get("marker_mtime")
    if marker is not None:
        if 0 <= (marker - failed_at).total_seconds() <= DEPLOY_COLLATERAL_WINDOW_SECS:
            return True
    return False


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


def _failed_alert(
    unit: dict, now: datetime, deploy: Optional[dict] = None
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
        p1_alert = _failed_alert(unit, now, deploy) or _flap_alert(unit, previous, now)
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
