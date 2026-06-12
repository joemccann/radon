"""CLI entry point — `python -m scripts.watchdog [...]`.

Subcommands:

  --bucket {intraday|continuous|daily|error}   run one bucket cycle
  ack <service> [--hours N] [--reason TEXT]    silence a service
  clear <service>                              remove its ack
  status                                       list active acks
"""
from __future__ import annotations

import argparse
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


def _cmd_bucket(args: argparse.Namespace) -> int:
    from scripts.watchdog import check, grouping, notify

    notify.log_startup_warning()
    now = datetime.now(timezone.utc)
    report = check.check_bucket(bucket=args.bucket, now=now)
    if not report.ran:
        print(f"[watchdog] bucket={args.bucket} skipped (off-window)")
        return 0

    fired = [o for o in report.outcomes if o.fired]
    for outcome in report.outcomes:
        line = f"  {outcome.service:24s} {outcome.status:8s} fired={outcome.fired}"
        print(line)

    # Systemd unit flap/failure alarm rides the continuous timer only —
    # one probe per 5 min is enough and keeps the other buckets pure
    # service_health checks. ALERT-ONLY; see scripts/watchdog/units.py.
    if args.bucket == "continuous":
        from scripts.watchdog import units
        unit_outcomes = units.check_units(now=now)
        for outcome in unit_outcomes:
            print(f"  {outcome.service:24s} {outcome.status:8s} fired={outcome.fired}")
        fired.extend(unit_outcomes)

    # Root-cause-aware dispatch: when IB Gateway is the upstream root
    # cause (auth_state ∈ awaiting_2fa, unreachable) AND ≥2 IB-dependent
    # services degraded in this cycle, collapse them into one Pushover
    # message + individual service_health rows. Otherwise per-service
    # cooldown-gated dispatch fires normally. See scripts/watchdog/grouping.py.
    grouping.dispatch_with_grouping(outcomes=fired, now=now)

    # The daily bucket carries the once-per-UTC-day P2/P3 digest flush
    # (DUR-14). On send failure flush_daily_digest records the dispatcher
    # error on the watchdog-alerts row itself and we must not overwrite
    # it with the quiet-cycle ok heartbeat below; pending entries retry
    # on the next hourly daily-bucket run.
    digest_error = None
    if args.bucket == "daily":
        digest_error = notify.flush_daily_digest(now=now)

    print(f"[watchdog] bucket={args.bucket} fired={len(fired)}/{len(report.outcomes)}")

    # Heartbeat the watchdog-alerts row when this bucket cycle dispatched
    # nothing. notify._emit_service_health() writes ``error`` on every
    # fire but never writes ``ok`` between fires — without this, a single
    # alert latches the row forever and the banner keeps showing
    # watchdog-alerts even after the underlying issue heals. Same
    # heartbeat-on-success pattern as replica-watchdog (see
    # feedback_service_health_heartbeat.md).
    if not fired and not digest_error:
        notify.heartbeat_ok(bucket=args.bucket, now=now)
    return 0


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
