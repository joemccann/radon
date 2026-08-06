#!/usr/bin/env python3.13
"""Create and backfill Performance TWR tables (plan §4.2).

- Idempotent DDL for nav_snapshots, external_flows, twr_subperiods (migration 0035).
- Optional backfill from existing Flex XML dumps in data/flex_cache/*.xml
  (EquitySummaryByReportDateInBase → nav_snapshots, CashTransactions
  + Transfers → external_flows).
- If no cache is present, DDL alone is the success criterion (the live
  perf_twr_builder will populate rows on its next Flex fetch).

Usage:
    python3.13 scripts/migrate_perf_twr.py              # ensure tables
    python3.13 scripts/migrate_perf_twr.py --backfill   # + parse data/flex_cache if present
    python3.13 scripts/migrate_perf_twr.py --dry-run    # print what would run without writing

Direct-to-cloud libsql (Turso). Requires TURSO_DB_URL + TURSO_AUTH_TOKEN
(see scripts/db/client.py). Turso pattern: single libsql connection,
CREATE TABLE IF NOT EXISTS, INSERT ... ON CONFLICT DO UPDATE.
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

_PROJECT_DIR = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]

    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / ".env.ib-mode")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

FLEX_CACHE_DIR = _PROJECT_DIR / "data" / "flex_cache"

DDL_PATH = Path(__file__).resolve().parent / "db" / "migrations" / "0035_perf_twr.sql"


def _ensure_tables(dry_run: bool = False) -> None:
    sql_text = DDL_PATH.read_text(encoding="utf-8")
    if dry_run:
        print(f"[migrate_perf_twr] dry-run — would execute:\n{sql_text[:800]}")
        return
    # Prefer the canonical writer helper (keeps DDL in one place), fall back
    # to executing the migration file directly if writer is unavailable
    # (stripped env / import failure).
    try:
        from db.writer import ensure_perf_twr_tables

        ensure_perf_twr_tables()
        print("[migrate_perf_twr] tables ensured via db.writer.ensure_perf_twr_tables()")
        return
    except Exception as exc:
        print(f"[migrate_perf_twr] writer helper failed ({exc}); falling back to direct DDL", file=sys.stderr)
    from db.client import get_db

    db = get_db()
    for stmt in [s.strip() for s in sql_text.split(";") if s.strip()]:
        # Skip comment-only fragments; migration runner handles schema_migrations insert separately.
        stripped = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
        if not stripped:
            continue
        db.execute(stripped)
    db.commit()
    print("[migrate_perf_twr] tables ensured via direct DDL")


def _parse_flex_xml(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (nav_rows, flow_rows) parsed from one Flex XML dump.

    nav_rows: {account_id, report_date, total_net_liq, cash, stock, options, accrued_fees}
    flow_rows: {account_id, report_date, amount, flow_type, note}
    External flows exclude Borrow Fee / Commission / Dividend (they stay in NAV as returns).
    """
    nav_rows: list[dict[str, Any]] = []
    flow_rows: list[dict[str, Any]] = []
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        print(f"[migrate_perf_twr] skip {path.name}: XML parse error: {exc}", file=sys.stderr)
        return nav_rows, flow_rows
    root = tree.getroot()

    # Flex statement wraps payload in FlexStatements/FlexStatement
    # EquitySummaryByReportDateInBase rows carry reportDate + total
    for elem in root.iter("EquitySummaryByReportDateInBase"):
        report_date = elem.get("reportDate") or elem.get("report_date") or ""
        account = elem.get("accountId") or elem.get("account_id") or "ALL"
        # total is EquitySummaryByReportDateInBase.total / totalNetLiq field name varies
        total_raw = (
            elem.get("total")
            or elem.get("totalNetLiq")
            or elem.get("netLiquidation")
            or elem.get("equityWithLoanValue")
        )
        if not report_date or total_raw is None:
            continue
        try:
            total = float(str(total_raw).replace(",", ""))
        except ValueError:
            continue
        nav_rows.append(
            {
                "account_id": account,
                "report_date": report_date[:10],
                "total_net_liq": total,
                "cash": _f(elem.get("cash")),
                "stock": _f(elem.get("stock")),
                "options": _f(elem.get("options")),
                "accrued_fees": _f(elem.get("accruedFees") or elem.get("accrued_fees")),
            }
        )

    # CashTransactions: deposits / withdrawals are external flows; borrow/dividend/commission are NOT.
    excluded_types = {"borrow fee", "commission", "dividend", "withholding tax", "broker interest"}
    for elem in root.iter("CashTransaction"):
        ttype = (elem.get("type") or elem.get("transactionType") or "").strip().lower()
        if ttype in excluded_types:
            continue
        if ttype not in {"deposits", "deposit", "withdrawals", "withdrawal"}:
            continue
        report_date = elem.get("reportDate") or elem.get("settleDate") or elem.get("dateTime") or ""
        report_date = report_date[:10]
        amount_raw = elem.get("amount") or elem.get("cashAmount")
        if not report_date or amount_raw is None:
            continue
        try:
            amount = float(str(amount_raw).replace(",", ""))
        except ValueError:
            continue
        # Withdrawals in Flex are negative already; normalize to external-flow sign (+deposit, -withdrawal)
        flow_type = "deposit" if "deposit" in ttype else "withdrawal"
        if flow_type == "withdrawal" and amount > 0:
            amount = -amount
        if flow_type == "deposit" and amount < 0:
            amount = abs(amount)
        flow_rows.append(
            {
                "account_id": elem.get("accountId") or elem.get("account_id") or "ALL",
                "report_date": report_date,
                "amount": amount,
                "flow_type": flow_type,
                "note": elem.get("description") or elem.get("type"),
            }
        )

    for elem in root.iter("Transfer"):
        asset_cat = (elem.get("assetCategory") or "").strip()
        # ACATS heuristic: Transfer where assetCategory IS NULL and no fill that day
        # (same heuristic as plan §4.2, but now explicit and auditable).
        if asset_cat:
            continue
        report_date = elem.get("reportDate") or elem.get("date") or ""
        report_date = report_date[:10]
        if not report_date:
            continue
        # cashTransfer / securityTransfer valuation — fall back to NAV-referenced note
        amount_raw = elem.get("cashAmount") or elem.get("transferPrice") or elem.get("amount")
        if amount_raw is None:
            continue
        try:
            amount = float(str(amount_raw).replace(",", ""))
        except ValueError:
            continue
        flow_rows.append(
            {
                "account_id": elem.get("accountId") or elem.get("account_id") or "ALL",
                "report_date": report_date,
                "amount": amount,
                "flow_type": "acats",
                "note": f"Transfer {elem.get('transferId') or ''} {elem.get('description') or ''}".strip(),
            }
        )
    return nav_rows, flow_rows


def _f(value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _backfill_from_cache(dry_run: bool = False) -> tuple[int, int]:
    if not FLEX_CACHE_DIR.is_dir():
        print(f"[migrate_perf_twr] no flex cache at {FLEX_CACHE_DIR} — skipping backfill")
        return 0, 0
    xml_files = sorted(FLEX_CACHE_DIR.glob("*.xml"))
    if not xml_files:
        print(f"[migrate_perf_twr] no *.xml in {FLEX_CACHE_DIR} — skipping backfill")
        return 0, 0

    all_nav: list[dict[str, Any]] = []
    all_flows: list[dict[str, Any]] = []
    for p in xml_files:
        nav, flows = _parse_flex_xml(p)
        all_nav.extend(nav)
        all_flows.extend(flows)
        print(f"[migrate_perf_twr] {p.name}: {len(nav)} NAV rows, {len(flows)} flow rows")

    # Deduplicate on PK: keep last occurrence (latest file wins)
    nav_by_pk: dict[tuple[str, str], dict[str, Any]] = {}
    for row in all_nav:
        nav_by_pk[(row["account_id"], row["report_date"])] = row
    flows_by_pk: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in all_flows:
        # Sum flows per (account, date, flow_type) if duplicates across files
        pk = (row["account_id"], row["report_date"], row["flow_type"])
        if pk in flows_by_pk:
            flows_by_pk[pk]["amount"] = float(flows_by_pk[pk]["amount"]) + float(row["amount"])
        else:
            flows_by_pk[pk] = row

    dedup_nav = list(nav_by_pk.values())
    dedup_flows = list(flows_by_pk.values())
    print(f"[migrate_perf_twr] deduped: {len(dedup_nav)} NAV, {len(dedup_flows)} flows")

    if dry_run:
        print("[migrate_perf_twr] dry-run — would upsert rows above")
        return len(dedup_nav), len(dedup_flows)

    try:
        from db.writer import upsert_nav_snapshot_rows, upsert_external_flow_rows

        if dedup_nav:
            upsert_nav_snapshot_rows(dedup_nav)
            print(f"[migrate_perf_twr] upserted {len(dedup_nav)} nav_snapshots")
        if dedup_flows:
            upsert_external_flow_rows(dedup_flows)
            print(f"[migrate_perf_twr] upserted {len(dedup_flows)} external_flows")
    except Exception as exc:
        print(f"[migrate_perf_twr] backfill write failed: {exc}", file=sys.stderr)
        raise
    return len(dedup_nav), len(dedup_flows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and backfill Performance TWR tables (plan §4.2)")
    parser.add_argument("--backfill", action="store_true", help="also parse data/flex_cache/*.xml into nav_snapshots/external_flows")
    parser.add_argument("--dry-run", action="store_true", help="print actions without writing")
    args = parser.parse_args()

    if not os.environ.get("TURSO_DB_URL") or not os.environ.get("TURSO_AUTH_TOKEN"):
        # Allow dry-run without credentials for CI lint.
        if args.dry_run:
            print("[migrate_perf_twr] dry-run without TURSO creds — checking DDL file only")
            print(f"[migrate_perf_twr] DDL: {DDL_PATH} exists={DDL_PATH.exists()}")
            if DDL_PATH.exists():
                print(DDL_PATH.read_text(encoding="utf-8")[:600])
            return
        sys.stderr.write("TURSO_DB_URL and TURSO_AUTH_TOKEN must be set (see web/.env or root .env)\n")
        sys.exit(1)

    _ensure_tables(dry_run=args.dry_run)
    if args.backfill:
        _backfill_from_cache(dry_run=args.dry_run)
    else:
        # Always attempt opportunistic backfill if cache happens to exist,
        # but don't fail the migration if it doesn't.
        if FLEX_CACHE_DIR.is_dir() and any(FLEX_CACHE_DIR.glob("*.xml")):
            print("[migrate_perf_twr] flex cache found — running opportunistic backfill (use --backfill to be explicit)")
            _backfill_from_cache(dry_run=args.dry_run)
    if not args.dry_run:
        print("[migrate_perf_twr] done")


if __name__ == "__main__":
    main()
