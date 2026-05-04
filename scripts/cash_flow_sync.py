#!/usr/bin/env python3
"""Cash-flow sync — pull deposits/withdrawals/dividends from IB Flex Query.

The existing `journal` table tracks executions only. Capital movements
(deposits, withdrawals, dividends paid, interest, fees) live in IB's
`CashTransaction` Flex section and aren't surfaced anywhere in radon yet.
This script bridges that gap.

Usage:
    python -m scripts.cash_flow_sync
    python -m scripts.cash_flow_sync --types Deposit,Withdrawal
    python -m scripts.cash_flow_sync --json     # print parsed rows, don't write

Required env (already set on Hetzner per /home/radon/radon-cloud/.env):
    IB_FLEX_TOKEN          - Flex Web Service token
    IB_FLEX_NAV_QUERY_ID   - Flex Query ID with CashTransaction section enabled

Outputs:
    Turso `cash_flows` table (one row per transactionID, idempotent).
    `data/cash_flows.json`    - file fallback / debug trace of last pull.

Cadence:
    monitor_daemon `cash_flow_sync` handler runs this daily (86400s). Cash
    transactions don't change intraday so polling more often is wasteful.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import urlopen

# Paths / sys.path
_SCRIPTS_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPTS_DIR.parent
_DATA_DIR = _PROJECT_DIR / "data"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / ".env.ib-mode")
except Exception:
    pass

# DB writer / atomic_io are imported lazily inside main() so pure functions
# (_classify, _normalize_date, fetch_cash_transactions) can be unit-tested
# without libsql_experimental installed in the test environment.

# Flex Web Service endpoints
_SEND_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
_GET_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"


def _classify(raw_type: str, amount: float) -> str:
    """Map IB's free-form `type` string to our normalized bucket.

    Buckets: Deposit, Withdrawal, Dividend, Interest, Fee, WithholdingTax, Other.
    For "Deposits/Withdrawals" combined rows, sign of amount disambiguates.
    """
    norm = (raw_type or "").strip().lower()

    # Combined "Deposits/Withdrawals" label: disambiguate by amount sign.
    # MUST come before the substring matchers below — otherwise the
    # "withdrawal" substring rule swallows positive deposits incorrectly.
    if "deposits/withdrawals" in norm or "deposits & withdrawals" in norm:
        return "Deposit" if amount >= 0 else "Withdrawal"
    if "withdrawal" in norm:
        return "Withdrawal"
    if "deposit" in norm:
        return "Deposit"
    if "dividend" in norm or "payment in lieu" in norm:
        return "Dividend"
    if "tax" in norm:
        return "WithholdingTax"
    if "interest" in norm:
        return "Interest"
    if "fee" in norm or "commission" in norm:
        return "Fee"
    return "Other"


def _normalize_date(raw: str) -> str:
    """IB sometimes uses 20260504 (compact) and sometimes 2026-05-04 (ISO)."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if len(raw) >= 8 and raw[:8].isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    return raw


def fetch_cash_transactions(token: str, query_id: str, *, max_polls: int = 20, poll_sleep: float = 3.0) -> list[dict[str, Any]]:
    """Fetch the NAV Flex Query and parse CashTransaction rows.

    Returns a list of dicts ready to feed `upsert_cash_flow`.
    """
    params = urlencode({"t": token, "q": query_id, "v": "3"})
    resp = urlopen(f"{_SEND_URL}?{params}", timeout=30)
    root = ET.fromstring(resp.read().decode("utf-8"))
    ref_node = root.find(".//ReferenceCode")
    if ref_node is None or not ref_node.text:
        raise RuntimeError("Flex SendRequest did not return a ReferenceCode")
    ref_code = ref_node.text

    xml_text = ""
    for _ in range(max_polls):
        time.sleep(poll_sleep)
        params2 = urlencode({"t": token, "q": ref_code, "v": "3"})
        resp2 = urlopen(f"{_GET_URL}?{params2}", timeout=30)
        xml_text = resp2.read().decode("utf-8")
        if "<FlexStatements" in xml_text:
            break
    else:
        raise RuntimeError(f"Flex statement not ready after {max_polls} polls")

    out: list[dict[str, Any]] = []
    root2 = ET.fromstring(xml_text)
    for ct in root2.findall(".//CashTransaction"):
        txn_id = (ct.get("transactionID") or "").strip()
        if not txn_id:
            continue
        amt = float(ct.get("amount") or 0.0)
        if amt == 0.0:
            continue
        raw_type = (ct.get("type") or "").strip()
        date_str = _normalize_date(ct.get("reportDate") or ct.get("dateTime") or "")
        out.append({
            "id": txn_id,
            "date": date_str,
            "type": _classify(raw_type, amt),
            "amount": amt,
            "currency": (ct.get("currency") or "USD").upper(),
            "description": (ct.get("description") or "").strip() or None,
            "raw_type": raw_type or None,
        })
    return out


def _filter_types(rows: list[dict[str, Any]], allowed: Optional[set[str]]) -> list[dict[str, Any]]:
    if not allowed:
        return rows
    return [r for r in rows if r["type"] in allowed]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync IB cash transactions to radon's cash_flows table")
    parser.add_argument(
        "--types",
        default="",
        help="Comma-separated normalized types to keep (Deposit,Withdrawal,Dividend,Interest,Fee,WithholdingTax,Other). Default: all.",
    )
    parser.add_argument("--json", action="store_true", help="Print parsed rows as JSON to stdout, do NOT write to DB.")
    parser.add_argument("--no-file", action="store_true", help="Skip writing data/cash_flows.json")
    args = parser.parse_args()

    token = os.environ.get("IB_FLEX_TOKEN")
    query_id = os.environ.get("IB_FLEX_NAV_QUERY_ID")
    if not token or not query_id:
        print("ERR: IB_FLEX_TOKEN / IB_FLEX_NAV_QUERY_ID not configured", file=sys.stderr)
        return 1

    allowed = {t.strip() for t in args.types.split(",") if t.strip()} or None

    try:
        rows = fetch_cash_transactions(token, query_id)
    except Exception as exc:
        print(f"ERR: cash flow fetch failed: {exc}", file=sys.stderr)
        return 1

    rows = _filter_types(rows, allowed)
    rows.sort(key=lambda r: (r["date"], r["id"]))

    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        print()
        return 0

    # Lazy DB imports so pure functions remain unit-testable without libsql.
    from db.writer import upsert_cash_flow
    from utils.atomic_io import atomic_save

    written = 0
    for r in rows:
        upsert_cash_flow(
            txn_id=r["id"],
            date_str=r["date"],
            txn_type=r["type"],
            amount=r["amount"],
            currency=r["currency"],
            description=r["description"],
            raw_type=r["raw_type"],
        )
        written += 1

    if not args.no_file:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "synced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "count": len(rows),
            "rows": rows,
        }
        atomic_save(_DATA_DIR / "cash_flows.json", snapshot)

    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    print(f"Synced {written} cash flows. Breakdown: {by_type}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
