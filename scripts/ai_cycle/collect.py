"""Daily ingestion and resumable bounded historical collection.

Examples: python -m scripts.ai_cycle --verify --sources vercel,gpu-rental
          python -m scripts.ai_cycle --record --database /tmp/ai-cycle.db
          python -m scripts.ai_cycle --record --backfill --start 2026-01-01
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .collectors import SourceError, Transport, archive_raw, collect_source, now_iso, parse_disclosures

SOURCES = (
    "openrouter",
    "vercel",
    "gpu-rental",
    "artificial-analysis",
    "sec",
    "eia",
    "vast",
    "noaa",
    "portkey",
    "issuer-disclosures",
    "lambda",
)
KEYS = ("OPENROUTER_API_KEY", "ARTIFICIAL_ANALYSIS_API_KEY", "SEC_USER_AGENT", "EIA_API_KEY", "VAST_API_KEY")
OPERATOR_FIELDS = (*KEYS, "RADON_AI_CYCLE_AA_BASKET")


def windows(start, end, days=28):
    current, last = date.fromisoformat(start), date.fromisoformat(end)
    if current > last or not 1 <= days <= 90:
        raise ValueError("Invalid collection window")
    while current <= last:
        stop = min(current + timedelta(days=days - 1), last)
        yield current.isoformat(), stop.isoformat()
        current = stop + timedelta(days=1)


def environment(env_file=None):
    result = {key: os.environ[key] for key in OPERATOR_FIELDS if os.environ.get(key)}
    if env_file:
        from dotenv import dotenv_values

        values = dotenv_values(env_file)
        for key in OPERATOR_FIELDS:
            if key not in result and values.get(key):
                result[key] = values[key]
    # The Profile credential store is the operator source of truth. Scheduled
    # collectors are separate processes, so they must read it explicitly
    # rather than relying on the FastAPI process's in-memory environment.
    if os.environ.get("RADON_SECRET_STORE_PATH"):
        from secret_store import SecretStore

        store = SecretStore()
        for key in OPERATOR_FIELDS:
            stored = store.get_secret(key)
            if stored:
                result[key] = stored
    return result


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--record", action="store_true", help="Append observations to selected storage")
    mode.add_argument("--verify", action="store_true", help="Probe without database writes (default)")
    parser.add_argument("--database", help="Isolated SQLite path; omitted uses production only with --record")
    parser.add_argument("--archive", default=str(Path.home() / ".radon/ai-cycle/raw"))
    parser.add_argument("--env-file", help="Load only named provider keys; never print secrets")
    parser.add_argument("--sources", default=",".join(SOURCES))
    parser.add_argument("--start")
    parser.add_argument("--end", default=(datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat())
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument(
        "--checkpoint", help="Resume completed source windows; only records successful committed windows"
    )
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument(
        "--basket",
        default="",
        help="Comma-separated verified AA model slugs; immutable across a cohort",
    )
    parser.add_argument("--import-disclosures", help="Verified issuer observations JSON")
    args = parser.parse_args(argv)
    selected = args.sources.split(",")
    if any(source not in SOURCES for source in selected):
        parser.error("Unknown source")
    if not 1 <= args.max_requests <= 400:
        parser.error("--max-requests must be 1..400")
    end = date.fromisoformat(args.end)
    if end >= datetime.now(timezone.utc).date():
        parser.error("--end must be a completed UTC date")
    start = args.start or (end - timedelta(days=6)).isoformat()
    if args.backfill and not args.start:
        parser.error("--backfill requires --start")
    if (end - date.fromisoformat(start)).days > 3660:
        parser.error("Backfill is bounded to ten years per invocation")
    if date.fromisoformat(start) > end:
        parser.error("--start must precede --end")
    if not args.backfill and (end - date.fromisoformat(start)).days > 89:
        parser.error("Use --backfill for windows longer than 90 days")
    transport = Transport(Path(args.archive), max_requests=args.max_requests)
    env = environment(args.env_file)
    basket = args.basket or env.get("RADON_AI_CYCLE_AA_BASKET", "")
    store = None
    if args.record:
        from .store import ObservationStore

        store = ObservationStore(args.database)
        store.initialize()
    checkpoint = Path(args.checkpoint) if args.checkpoint else None
    completed = set(json.loads(checkpoint.read_text())) if checkpoint and checkpoint.exists() else set()
    report, row_count = [], 0
    periods = list(windows(start, args.end)) if args.backfill else [(start, args.end)]
    for source in selected:
        # Event-only disclosures have no provider check without an explicit import.
        # Preserve their last reviewed status and publication vintage on daily runs.
        if source == "issuer-disclosures" and not args.import_disclosures:
            continue
        # Snapshot-only feeds cannot reconstruct history by repeated current fetches.
        source_windows = periods if source in ("openrouter", "vercel", "eia") else [(start, args.end)]
        for first, last in source_windows:
            key = f"{source}:{first}:{last}"
            if key in completed:
                continue
            checked = now_iso()
            try:
                if source == "issuer-disclosures" and args.import_disclosures:
                    raw = Path(args.import_disclosures).read_bytes()
                    digest = archive_raw(Path(args.archive), raw)
                    rows = parse_disclosures(json.loads(raw), digest, checked)
                else:
                    effective_start = (
                        (end - timedelta(days=800)).isoformat() if source == "sec" and not args.start else first
                    )
                    rows = collect_source(
                        source,
                        transport,
                        effective_start,
                        last,
                        env=env,
                        basket=tuple(filter(None, basket.split(","))),
                    )
                status = dict(
                    source_id=source,
                    status="available" if rows else "unavailable",
                    reason=f"{len(rows)} observations; source schema validated"
                    if rows
                    else "No observations in completed window",
                    checked_at=checked,
                )
                if store:
                    store.append_observations(rows)
                    if rows:
                        completed.add(key)
                        if checkpoint:
                            checkpoint.parent.mkdir(parents=True, exist_ok=True)
                            temp = checkpoint.with_suffix(".tmp")
                            temp.write_text(json.dumps(sorted(completed)))
                            temp.replace(checkpoint)
                row_count += len(rows)
            except (SourceError, KeyError, TypeError, ValueError, OSError) as exc:
                reason = (
                    str(exc) if isinstance(exc, SourceError) else "Source schema or local archive validation failed"
                )
                state = (
                    "restricted"
                    if "HTTP 401" in reason or "HTTP 403" in reason
                    else (
                        "error"
                        if "HTTP " in reason or "transport failed" in reason or "validation failed" in reason
                        else "unavailable"
                    )
                )
                status = dict(source_id=source, status=state, reason=reason, checked_at=checked)
            if store:
                store.record_source_status(status)
            report.append({**status, "start": first, "end": last})
    print(
        json.dumps(
            {
                "mode": "record" if args.record else "verify",
                "observations": row_count,
                "requests": transport.requests,
                "sources": report,
            },
            indent=2,
        )
    )
    return int(any(item["status"] == "error" for item in report))


def main(argv=None):
    import sys

    flags = list(sys.argv[1:] if argv is None else argv)
    production = (
        "--record" in flags
        and not any(flag == "--database" or flag.startswith("--database=") for flag in flags)
        and not os.environ.get("RADON_AI_CYCLE_DB_PATH")
    )
    started = now_iso()
    try:
        code = _main(flags)
    except BaseException:
        if production:
            from scripts.db.hrana_http import write_service_health_http

            write_service_health_http(
                "ai-cycle",
                "error",
                started_at=started,
                finished_at=now_iso(),
                error={"message": "Collection failed; inspect sanitized source statuses"},
                timeout=8,
            )
        raise
    if production:
        from scripts.db.hrana_http import write_service_health_http

        write_service_health_http(
            "ai-cycle",
            "ok" if code == 0 else "error",
            started_at=started,
            finished_at=now_iso(),
            error=None if code == 0 else {"message": "Enabled publisher collection failed; inspect source statuses"},
            timeout=8,
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
