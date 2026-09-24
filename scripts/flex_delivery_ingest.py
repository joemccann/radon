#!/usr/bin/env python3
"""Ingest a saved Flex XML from a local directory. No Flex Web Service.

Timer is not installed. Call with --file or --inbox.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib.flex_classify import (
    ACTIVITY,
    TRADES,
    FlexClassifyError,
    classify_flex_xml,
    statement_metadata,
)


def _sha256(xml_text: str) -> str:
    return hashlib.sha256(xml_text.encode("utf-8")).hexdigest()


def claim_flex_delivery(content_sha256: str, **kwargs: Any) -> bool:
    """Indirection so the claim is injectable in tests. See db.writer."""
    from db.writer import claim_flex_delivery as _claim  # noqa: PLC0415

    return _claim(content_sha256, **kwargs)


def release_flex_delivery(content_sha256: str) -> bool:
    """Indirection so the release is injectable in tests. See db.writer."""
    from db.writer import release_flex_delivery as _release  # noqa: PLC0415

    return _release(content_sha256)


def flex_delivery_status(content_sha256: str) -> str | None:
    """Indirection so the lookup is injectable in tests. See db.writer."""
    from db.writer import flex_delivery_status as _status  # noqa: PLC0415

    return _status(content_sha256)


def mark_flex_delivery_applied(content_sha256: str) -> bool:
    """Indirection so the mark is injectable in tests. See db.writer."""
    from db.writer import mark_flex_delivery_applied as _mark  # noqa: PLC0415

    return _mark(content_sha256)


# The catalogued key whose timer drives this ingest in production. A release
# failure pages on it rather than on a new key: an uncatalogued key is invisible
# and a scheduled one for a no-cadence signal ages to stale and pages forever.
HEALTH_SERVICE = "flex-pull"
# The cash-flows row. The monitor daemon's cash_flow_sync handler wrote it until
# 2026-09-02 by spawning a no-source SendRequest run that exited
# EXIT_FLEX_SEND_DISABLED daily. This ingest is the only path that writes
# `cash_flows`, so it owns the heartbeat the watchdog's daily window and the
# `/cash-flows` lozenge read.
CASH_FLOW_HEALTH_SERVICE = "cash-flow-sync"


def delivery_rows_present(kind: str, xml_text: str) -> bool:
    """Independently verify duplicate coverage over bounded, read-only Hrana."""
    from db.hrana_http import hrana_query

    deadline = time.monotonic() + 15.0

    def query(sql, args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("duplicate coverage verification budget exhausted")
        return hrana_query(sql, args, timeout=min(4.0, remaining))

    if kind == ACTIVITY:
        from cash_flow_sync import parse_cash_transactions
        from perf_twr_builder import DEFAULT_ACCOUNT_ID, parse_nav_entries

        expected = parse_cash_transactions(xml_text)
        for offset in range(0, len(expected), 100):
            batch = expected[offset:offset + 100]
            ids = tuple(row["id"] for row in batch)
            rows = query(
                "SELECT id FROM cash_flows WHERE id IN ("
                + ",".join("?" for _ in ids) + ")", ids,
            )
            if not set(ids).issubset({row[0] for row in rows}):
                return False
        # The NAV query can legitimately contain no cash movements. Its
        # persisted dates must still exist. Values may have been corrected by
        # a later statement, so coverage does not compare historical amounts.
        dates = list(parse_nav_entries(xml_text))
        for offset in range(0, len(dates), 100):
            batch = dates[offset:offset + 100]
            rows = query(
                "SELECT report_date FROM nav_snapshots WHERE account_id = ? AND report_date IN ("
                + ",".join("?" for _ in batch) + ")", (DEFAULT_ACCOUNT_ID, *batch),
            )
            if not set(batch).issubset({row[0] for row in rows}):
                return False
        return True
    if kind == TRADES:
        return _trade_delivery_covered(xml_text, query)
    return False


# A later unread page can change rehydrate's prior-quantity label and turn a
# legacy-key skip into an import. Hitting the page cap with ids still pending
# is unknown coverage, never evidence that the missing rows were written.
_JOURNAL_COVERAGE_PAGE = 200
_JOURNAL_COVERAGE_PAGES = 100


def _trade_delivery_covered(xml_text: str, query) -> bool:
    """True when the journal writer would import nothing from this file.

    Exec-id presence misses fills the writer skips on purpose: superseded
    corrections, empty buckets, legacy (ticker, date, structure) duplicates,
    and Flex rows whose contract-day already matches individual IB fills.
    Those ids never land, so every re-sync of an applied file stayed
    unverified. `rehydrate_from_executions` is the oracle. A qty or notional
    disagreement stays unverified: the writer also refuses to book it, but
    the file does not match the journal.
    """
    from trade_blotter.flex_query import FlexQueryFetcher
    from journal_rehydrate import (
        _existing_exec_ids,
        _existing_exec_roots,
        _root_already_journaled,
        rehydrate_from_executions,
    )

    executions, dropped = FlexQueryFetcher(token="x", query_id="x").parse_xml_with_drops(xml_text)
    if dropped:
        return False
    pending = {str(execution.exec_id) for execution in executions}
    if not pending:
        return True

    trades: List[Dict[str, Any]] = []
    cursor = ""
    for _ in range(_JOURNAL_COVERAGE_PAGES):
        rows = query(
            "SELECT trade_id, payload FROM journal WHERE trade_id > ? "
            f"ORDER BY trade_id LIMIT {_JOURNAL_COVERAGE_PAGE}",
            (cursor,),
        )
        if not rows:
            break
        payloads = [json.loads(row[1]) for row in rows]
        trades.extend(payloads)
        ids = _existing_exec_ids(trades)
        roots = _existing_exec_roots(ids)
        if all(
            eid in ids or _root_already_journaled(eid, roots) for eid in pending
        ):
            return True
        if len(rows) < _JOURNAL_COVERAGE_PAGE:
            break
        cursor = rows[-1][0]
    else:
        return False

    updated, imported, _skipped, _latest = rehydrate_from_executions(
        executions, {"trades": trades}
    )
    return imported == 0 and not updated.get("aggregate_disagreements")


def ingest_xml(
    xml_text: str, *, source_path: str = "", record_as: str | None = None
) -> Dict[str, Any]:
    """`source_path` is the path the writers READ; `record_as` is what the claim
    records. They differ for the sFTP puller, which reads from a private temp
    file it then unlinks — recording that made `flex_deliveries.source_path`, the
    only column linking a fingerprint back to a delivered statement, a dead
    `/tmp/...` inside the unit's PrivateTmp namespace. R-419.
    """
    kind = classify_flex_xml(xml_text)
    digest = _sha256(xml_text)
    meta = statement_metadata(xml_text)
    # BEFORE any writer: re-applying a statement is not idempotent (R-329), so
    # the fingerprint has to gate the run rather than annotate it. R-326.
    if not claim_flex_delivery(
        digest,
        classified_as=kind,
        period_from=meta["period_from"],
        period_to=meta["period_to"],
        source_path=record_as or source_path or None,
    ):
        # A lost claim is `duplicate` only when the earlier run finished. A
        # fresh `in_progress` lease belongs to a run that is still going, or
        # to one whose release failed with its writer (R-436); reporting it
        # `ok` lets the 08:30 re-pull heartbeat fine over a half-applied
        # `cash_flows`. It becomes claimable again once stale.
        status = flex_delivery_status(digest)
        if status == "in_progress":
            return {
                "ok": False,
                "outcome": "in_progress",
                "classified_as": kind,
                "content_sha256": digest,
                "error": "claim is in_progress from an earlier run; retried once stale",
                "source_path": source_path,
            }
        try:
            if (
                status == "applied"
                and kind == ACTIVITY
                and not delivery_rows_present(kind, xml_text)
            ):
                # Suppressed TWR persist writes no series, so an applied
                # activity claim can lack its NAV dates. Insert the missing
                # ones. Cash that is absent stays unverified; do not replay.
                _repair_unmirrored_activity_nav(xml_text)
            confirmed = status == "applied" and delivery_rows_present(kind, xml_text)
        except Exception as exc:
            # A verification read failure does not authorize replay of the
            # non-idempotent ingest; report unknown coverage and retain its claim.
            confirmed = False
            print(f"[flex-ingest] duplicate coverage unavailable: {exc}", file=sys.stderr)
        if not confirmed:
            message = "applied delivery lacks verified persisted-row coverage; operator reconciliation required"
            if kind == ACTIVITY:
                _heartbeat_cash_flow_sync("error", {"message": message})
            return {"ok": False, "outcome": "coverage_unverified", "persistence_confirmed": False,
                    "classified_as": kind, "content_sha256": digest, "error": message,
                    "source_path": source_path}
        if kind == ACTIVITY:
            # Its cash flows are already in Turso. The 08:30 re-pull and every
            # manual re-run land here, so without this the row the lozenge
            # reads keeps whatever error was written last, over data that is
            # current. flex-pull's own row still calls a duplicate-only run
            # stale (R-389); that is a delivery signal, not a cash-flows one.
            _heartbeat_cash_flow_sync("ok")
        return {
            "ok": True,
            "outcome": "duplicate",
            "persistence_confirmed": True,
            "classified_as": kind,
            "content_sha256": digest,
            "source_path": source_path,
        }
    # The claim is a LEASE on work in progress, not a record that the work
    # succeeded. An ingest that fails or raises hands it back, or the same bytes
    # become permanently unretryable behind a green heartbeat while `cash_flows`
    # stays half-applied. R-379.
    try:
        result = _apply_classified(kind, xml_text, digest, source_path)
    except BaseException:
        _release_claim(digest)
        raise
    if not result.get("ok"):
        _release_claim(digest)
        return result
    # Only now has every writer committed. R-436.
    mark_flex_delivery_applied(digest)
    return result


def _repair_unmirrored_activity_nav(xml_text: str) -> None:
    """Insert statement NAV dates a suppressed TWR persist did not mirror.

    Cash coverage is the non-idempotent write. When those ids are present and
    the NAV dates are not, the 08:30 retry failed the oneshot
    (coverage_unverified, classified_as=activity). Insert the missing dates
    only. ON CONFLICT DO NOTHING so a later correction already stored under
    the same key is not overwritten.
    """
    from cash_flow_sync import parse_cash_transactions
    from db.hrana_http import hrana_execute, hrana_query
    from perf_twr_builder import DEFAULT_ACCOUNT_ID, parse_nav_entries

    deadline = time.monotonic() + 15.0

    def query(sql: str, args: tuple) -> list:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("duplicate coverage verification budget exhausted")
        return hrana_query(sql, args, timeout=min(4.0, remaining))

    expected = parse_cash_transactions(xml_text)
    for offset in range(0, len(expected), 100):
        batch = expected[offset:offset + 100]
        ids = tuple(row["id"] for row in batch)
        rows = query(
            "SELECT id FROM cash_flows WHERE id IN ("
            + ",".join("?" for _ in ids) + ")",
            ids,
        )
        if not set(ids).issubset({row[0] for row in rows}):
            return
    nav = parse_nav_entries(xml_text)
    dates = list(nav)
    missing: List[str] = []
    for offset in range(0, len(dates), 100):
        batch = dates[offset:offset + 100]
        rows = query(
            "SELECT report_date FROM nav_snapshots WHERE account_id = ? "
            "AND report_date IN (" + ",".join("?" for _ in batch) + ")",
            (DEFAULT_ACCOUNT_ID, *batch),
        )
        found = {row[0] for row in rows}
        missing.extend(day for day in batch if day not in found)
    for offset in range(0, len(missing), 100):
        batch = missing[offset:offset + 100]
        params: List[Any] = []
        for day in batch:
            params.extend((DEFAULT_ACCOUNT_ID, day, float(nav[day])))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("duplicate coverage verification budget exhausted")
        hrana_execute(
            "INSERT INTO nav_snapshots (account_id, report_date, total_net_liq, "
            "cash, stock, options, accrued_fees) VALUES "
            + ", ".join("(?, ?, ?, NULL, NULL, NULL, NULL)" for _ in batch)
            + " ON CONFLICT(account_id, report_date) DO NOTHING",
            params,
            timeout=min(4.0, remaining),
        )


def _release_claim(digest: str) -> None:
    """Best-effort release. A failure here must not mask the ingest failure.

    It is not silent either: the lease it leaves behind blocks the same bytes
    until it goes stale, so the failure pages on the flex-pull row. R-436.
    """
    try:
        release_flex_delivery(digest)
    except Exception as exc:  # noqa: BLE001 — the caller is already failing
        message = f"claim release failed for {digest}: {exc}"
        print(f"[flex-ingest] {message}", file=sys.stderr)
        _page_release_failure(digest, message)


def _page_release_failure(digest: str, message: str) -> None:
    try:
        from db import writer  # noqa: PLC0415

        writer.record_service_health(
            HEALTH_SERVICE,
            "error",
            finished_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            error={"message": message, "content_sha256": digest},
        )
    except Exception as exc:  # noqa: BLE001 — paging must not mask the ingest failure
        print(f"[flex-ingest] release-failure page non-fatal: {exc}", file=sys.stderr)


# REL-210 (R-584): once an error heartbeat is written in this process, a later
# duplicate's "ok" must not repaint the row green — the sftp batch iterates the
# remote listing unsorted and delivered files are never removed remotely, so a
# stale duplicate routinely sorts after a failing new statement.
_CASH_FLOW_ERROR_LATCHED = False


def _heartbeat_cash_flow_sync(state: str, error: Dict[str, Any] | None = None) -> None:
    global _CASH_FLOW_ERROR_LATCHED
    if state == "error":
        _CASH_FLOW_ERROR_LATCHED = True
    elif _CASH_FLOW_ERROR_LATCHED:
        print(
            "[flex-ingest] suppressing cash-flow-sync ok heartbeat: an error "
            "was recorded earlier in this run (REL-210)",
            file=sys.stderr,
        )
        return
    try:
        from db import writer  # noqa: PLC0415

        writer.record_service_health(
            CASH_FLOW_HEALTH_SERVICE,
            state,
            finished_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            error=error,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never masks the ingest result
        print(f"[flex-ingest] cash-flow-sync heartbeat non-fatal: {exc}", file=sys.stderr)


def _apply_classified(kind: str, xml_text: str, digest: str, source_path: str) -> Dict[str, Any]:
    if kind == ACTIVITY:
        import cash_flow_sync
        import perf_twr_builder

        if not source_path:
            raise FlexClassifyError("activity ingest requires a filesystem path")
        # R-609 (P1): only a non-zero RETURN used to arm the error heartbeat.
        # A RAISE out of the cash-flow write — a Turso/libsql error mid-chunk,
        # a parse error after the first chunk — released the claim, propagated
        # to the batch loop's per-file handler and wrote no heartbeat at all,
        # so the next stale duplicate painted `cash-flow-sync` green over a
        # half-applied `cash_flows`. Same failure, same latch.
        try:
            cash_code = cash_flow_sync.main(["--from-file", source_path, "--no-file"])
        except Exception as exc:
            _heartbeat_cash_flow_sync(
                "error",
                {
                    "message": f"cash_flow_sync --from-file raised: {type(exc).__name__}: {exc}",
                    "content_sha256": digest,
                },
            )
            raise
        if cash_code != 0:
            _heartbeat_cash_flow_sync(
                "error",
                {
                    "message": f"cash_flow_sync --from-file failed (exit {cash_code})",
                    "cash_exit": cash_code,
                    "content_sha256": digest,
                },
            )
            # `upsert_cash_flow_rows` chunks its writes, so a failure leaves the
            # earlier chunks committed. Building the TWR series over a
            # half-written `cash_flows` and persisting it as authoritative
            # turns a partial write into a published number. R-323.
            #
            # The claim was taken before the writers, so hand it back: without
            # this the operator's re-drop of the fixed file is a "duplicate"
            # no-op and the half-written chunks are never repaired. T-257.
            return {
                "ok": False,
                "classified_as": kind,
                "content_sha256": digest,
                "cash_exit": cash_code,
                "twr_status": None,
                "error": f"cash_flow_sync failed (exit {cash_code}); TWR not rebuilt",
                "source_path": source_path,
            }
        # REL-220 (R-588): this ok deliberately precedes the TWR build — it
        # reports the CASH-FLOW write, which is complete and (per R-329's
        # id-keyed upsert) convergent under the retry a TWR *exception*
        # triggers. A returned TWR status of degraded/unavailable is not an
        # ingest failure: persist already ran, and perf-twr owns that page.
        # 2026-09-16 page 5a2eb828: cash_exit=0 + twr_status=degraded failed
        # radon-flex-pull (Result=exit-code) while radon-perf-twr published
        # ok three minutes later.
        _heartbeat_cash_flow_sync("ok")
        twr = perf_twr_builder.build_and_persist(from_file=source_path, persist=True)
        return {
            "ok": True,
            "classified_as": kind,
            "content_sha256": digest,
            "cash_exit": cash_code,
            "twr_status": twr.get("status"),
            "source_path": source_path,
        }
    if kind == TRADES:
        import journal_rehydrate

        if not source_path:
            raise FlexClassifyError("trades ingest requires a filesystem path")
        result = journal_rehydrate.rehydrate(xml_text=xml_text)
        return {
            "ok": bool(result.get("ok")),
            "classified_as": kind,
            "content_sha256": digest,
            "imported": result.get("imported"),
            "skipped": result.get("skipped"),
            "error": result.get("error"),
            "source_path": source_path,
        }
    raise FlexClassifyError(kind)


def ingest_path(path: Path) -> Dict[str, Any]:
    xml_text = path.read_text(encoding="utf-8")
    return ingest_xml(xml_text, source_path=str(path))


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", metavar="PATH", help="One XML file")
    parser.add_argument("--inbox", metavar="DIR", help="Directory of *.xml (not a timer)")
    args = parser.parse_args(argv)
    if not args.file and not args.inbox:
        parser.error("pass --file or --inbox")
    paths: List[Path] = []
    if args.file:
        paths.append(Path(args.file))
    if args.inbox:
        paths.extend(sorted(Path(args.inbox).glob("*.xml")))
    results = []
    ok = True
    for path in paths:
        # Per-file isolation: earlier files have already mutated `cash_flows`,
        # `journal` and TWR by the time a later one fails, so letting anything
        # but a classify error escape aborted the batch with nothing printed
        # and the remaining files unprocessed. R-361.
        try:
            result = ingest_path(path)
        except FlexClassifyError as exc:
            result = {"ok": False, "error": str(exc), "source_path": str(path)}
        except Exception as exc:  # noqa: BLE001 — one bad file must not abort the batch
            result = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "source_path": str(path),
            }
        results.append(result)
        ok = ok and bool(result.get("ok"))
    print(json.dumps({"ok": ok, "results": results, "ran_at": datetime.now(timezone.utc).isoformat()}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
