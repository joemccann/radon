"""CLI entry point — `python -m scripts.watchdog [...]`.

Subcommands:

  --bucket {intraday|continuous|daily|error}   run one bucket cycle
  ack <service> [--hours N] [--reason TEXT]    silence a service
  clear <service>                              remove its ack
  status                                       list active acks
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Match the canonical sys.path pattern used by every other script entry point
# (cash_flow_sync.py, cri_scan.py, etc): prepend the scripts/ dir so the bare
# `from db.client import get_db` / `from db.writer import record_service_health`
# imports inside check.py / cooldown.py / ack.py / notify.py resolve. Without
# this, `python -m scripts.watchdog --bucket X` from systemd fails on the
# first cross-package import with ModuleNotFoundError: No module named 'db'.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# Load .env files so TURSO_* + channel creds are visible to subprocess runs.
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / ".env.ib-mode")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass


# REL-010: consecutive snapshot-failure counter + page cooldown live in a
# FILE because the failure mode being handled is "the DB is down".
_SNAPSHOT_FAILURE_STATE = _PROJECT_DIR / "data" / "watchdog_snapshot_failures.json"
SNAPSHOT_FAILURE_PAGE_THRESHOLD = 3
SNAPSHOT_PAGE_COOLDOWN_SECS = 2 * 3600


def _read_snapshot_failure_state() -> dict:
    try:
        return json.loads(_SNAPSHOT_FAILURE_STATE.read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt = fresh
        return {"consecutive": 0, "last_paged_at": None}


def _write_snapshot_failure_state(state: dict) -> None:
    try:
        _SNAPSHOT_FAILURE_STATE.parent.mkdir(parents=True, exist_ok=True)
        _SNAPSHOT_FAILURE_STATE.write_text(json.dumps(state))
    except Exception as exc:  # noqa: BLE001
        print(f"[watchdog] snapshot-failure state write failed: {exc}")


def _reset_snapshot_failure_counter() -> None:
    state = _read_snapshot_failure_state()
    if state.get("consecutive"):
        state["consecutive"] = 0
        _write_snapshot_failure_state(state)


def _handle_snapshot_unavailable(*, bucket: str, now: datetime, reason: str) -> int:
    """The DB is down: keep the DB-free checks alive and page when blind.

    Previously this printed "skipped (off-window)" and exited 0 — the
    units alarm, external-probe read, and every page were skipped exactly
    when the DB was the outage (R-021).
    """
    from scripts.watchdog import external_probe, notify, units

    print(f"[watchdog] bucket={bucket} SNAPSHOT UNAVAILABLE: {reason}")

    state = _read_snapshot_failure_state()
    state["consecutive"] = int(state.get("consecutive") or 0) + 1
    _write_snapshot_failure_state(state)

    # DB-free checks still run on the continuous cadence; fired outcomes
    # page directly (the cooldown table is unreachable).
    if bucket == "continuous":
        try:
            outcomes = list(units.check_units(now=now))
            outcomes.append(external_probe.check_external_probe(now=now))
            for outcome in outcomes:
                print(f"  {outcome.service:24s} {outcome.status:8s} fired={outcome.fired}")
                if outcome.fired:
                    err = notify.send_direct_page(
                        title=f"radon watchdog: {outcome.service}",
                        message=outcome.message or f"{outcome.service} degraded "
                                f"(paged direct — DB unavailable)",
                        tag=outcome.service,
                    )
                    if err:
                        print(f"  [direct-page] {outcome.service} failed: {err}")
        except Exception as exc:  # noqa: BLE001 — never mask the blind-page below
            print(f"[watchdog] DB-free checks failed: {exc}")

    if int(state["consecutive"]) >= SNAPSHOT_FAILURE_PAGE_THRESHOLD:
        last_paged = state.get("last_paged_at")
        due = True
        if last_paged:
            try:
                last_dt = datetime.fromisoformat(last_paged)
                due = (now - last_dt).total_seconds() >= SNAPSHOT_PAGE_COOLDOWN_SECS
            except ValueError:
                due = True
        if due:
            err = notify.send_direct_page(
                title="radon watchdog BLIND",
                message=(
                    f"service_health snapshot unavailable for "
                    f"{state['consecutive']} consecutive bucket runs — the "
                    f"on-box watchdog cannot see writer health ({reason})"
                ),
                tag="watchdog-blind",
            )
            if err:
                print(f"[watchdog] blind-page failed: {err}")
            else:
                state["last_paged_at"] = now.isoformat()
                _write_snapshot_failure_state(state)
    return 0


def _cmd_bucket(args: argparse.Namespace) -> int:
    from scripts.watchdog import check, grouping, notify

    notify.log_startup_warning()
    now = datetime.now(timezone.utc)
    report = check.check_bucket(bucket=args.bucket, now=now)
    if not report.ran:
        if report.skip_reason and "snapshot_unavailable" in report.skip_reason:
            return _handle_snapshot_unavailable(
                bucket=args.bucket, now=now, reason=report.skip_reason
            )
        print(f"[watchdog] bucket={args.bucket} skipped (off-window)")
        return 0

    _reset_snapshot_failure_counter()

    observed_outcomes = list(report.outcomes)
    for outcome in report.outcomes:
        line = f"  {outcome.service:24s} {outcome.status:8s} fired={outcome.fired}"
        print(line)

    # Systemd unit flap/failure alarm rides the continuous timer only —
    # one probe per 5 min is enough and keeps the other buckets pure
    # service_health checks. ALERT-ONLY; see scripts/watchdog/units.py.
    if args.bucket == "continuous":
        from scripts.watchdog import external_probe, units
        unit_outcomes = units.check_units(now=now)
        observed_outcomes.extend(unit_outcomes)
        for outcome in unit_outcomes:
            print(f"  {outcome.service:24s} {outcome.status:8s} fired={outcome.fired}")

        probe_outcome = external_probe.check_external_probe(now=now)
        observed_outcomes.append(probe_outcome)
        print(
            f"  {probe_outcome.service:24s} {probe_outcome.status:8s} "
            f"fired={probe_outcome.fired}"
        )

    fired = [outcome for outcome in observed_outcomes if outcome.fired]

    # Root-cause-aware dispatch: when IB Gateway is the upstream root
    # cause (auth_state ∈ awaiting_2fa, unreachable) AND ≥2 IB-dependent
    # services degraded in this cycle, collapse them into one Pushover
    # message + individual service_health rows. Otherwise per-service
    # cooldown-gated dispatch fires normally. See scripts/watchdog/grouping.py.
    dispatch_summary = grouping.dispatch_with_grouping(outcomes=fired, now=now)

    # Cancel-on-recovery: a P1 Pushover emergency re-alerts every 60s for a full
    # hour, even after the condition clears (a transient blip thus spams for an
    # hour). Once the service is healthy again — or IB is authenticated, for the
    # grouped key — cancel its emergency push so it stops paging.
    _reconcile_recovered_emergencies(outcomes=observed_outcomes, now=now)

    # The daily bucket carries the once-per-UTC-day P2/P3 digest flush
    # (DUR-14). On send failure flush_daily_digest records the dispatcher
    # error on the watchdog-alerts row itself and we must not overwrite
    # it with the quiet-cycle ok heartbeat below; pending entries retry
    # on the next hourly daily-bucket run.
    digest_error = None
    if args.bucket == "daily":
        digest_error = notify.flush_daily_digest(now=now)

    print(f"[watchdog] bucket={args.bucket} fired={len(fired)}/{len(observed_outcomes)}")

    # Heartbeat the watchdog-alerts row when this bucket cycle dispatched
    # nothing. notify._emit_service_health() writes ``error`` on every
    # fire but never writes ``ok`` between fires — without this, a single
    # alert latches the row forever and the banner keeps showing
    # watchdog-alerts even after the underlying issue heals. Same
    # heartbeat-on-success pattern as replica-watchdog (see
    # feedback_service_health_heartbeat.md).
    if not dispatch_summary.health_recorded and not digest_error:
        notify.heartbeat_ok(bucket=args.bucket, now=now)
    return 0


def _reconcile_recovered_emergencies(*, outcomes, now) -> None:
    """Cancel still-retrying P1 emergency pushes whose condition has recovered.

    Per-service: the service is healthy in this cycle's observations. Grouped
    (ib-gateway-grouped): IB is no longer in a grouping auth-state. Health is
    only fetched when a grouped emergency is actually active, so a quiet cycle
    costs one cheap cooldown read and no network."""
    from . import cooldown, grouping, notify

    active = cooldown.active_emergency_services(now=now)
    if not active:
        return
    healthy = {o.service for o in outcomes if o.status == "healthy"}
    for svc in active:
        if svc == grouping.GROUPED_ALERT_KEY:
            try:
                auth = (grouping.fetch_health() or {}).get("auth_state") or "unknown"
            except Exception:  # noqa: BLE001
                auth = "unknown"
            recovered = auth != "unknown" and auth not in grouping.GROUPING_AUTH_STATES
        else:
            recovered = svc in healthy
        if recovered:
            cancel_error = notify.cancel_emergency(svc)
            if not isinstance(cancel_error, str):
                cooldown.mark_emergency_resolved(service=svc)
                print(f"  [recovery] cancelled emergency push for {svc}")
            else:
                print(f"  [recovery] emergency cancellation failed for {svc}: {cancel_error}")


def _cmd_ack(args: argparse.Namespace) -> int:
    from scripts.watchdog import ack
    ack.add_ack(service=args.service, hours=args.hours, reason=args.reason)
    expires = ack.list_active_acks()
    matched = next((row for row in expires if row["service"] == args.service), None)
    if matched:
        print(f"[watchdog] acked {args.service} until {matched['expires_at']}")
    return 0


def _cmd_clear(args: argparse.Namespace) -> int:
    from scripts.watchdog import ack
    ack.clear_ack(service=args.service)
    print(f"[watchdog] cleared ack for {args.service}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from scripts.watchdog import ack
    rows = ack.list_active_acks()
    if not rows:
        print("[watchdog] no active acks")
        return 0
    print(f"[watchdog] {len(rows)} active ack(s):")
    for row in rows:
        reason = f" — {row['reason']}" if row["reason"] else ""
        print(f"  {row['service']:24s} expires {row['expires_at']}{reason}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="watchdog", description=__doc__)
    parser.add_argument(
        "--bucket",
        choices=["intraday", "continuous", "daily", "error"],
        help="Run one bucket cycle and exit.",
    )

    sub = parser.add_subparsers(dest="command")

    ack_p = sub.add_parser("ack", help="Silence a service for N hours.")
    ack_p.add_argument("service")
    ack_p.add_argument("--hours", type=int, default=4)
    ack_p.add_argument("--reason", default=None)

    clear_p = sub.add_parser("clear", help="Remove an ack.")
    clear_p.add_argument("service")

    sub.add_parser("status", help="List active acks.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.bucket:
        return _cmd_bucket(args)
    if args.command == "ack":
        return _cmd_ack(args)
    if args.command == "clear":
        return _cmd_clear(args)
    if args.command == "status":
        return _cmd_status(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
