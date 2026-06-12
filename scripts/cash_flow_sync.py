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
    monitor_daemon `cash_flow_sync` handler runs this once per ET trading
    day at 17:00 ET (1h after market close). IBKR Flex publishes cash
    transactions once per day with a ~1-day settlement lag — a single
    well-timed daily call after the publication window is sufficient.

    The 4h cadence used through 2026-05-08 fired up to 12 attempts per
    day; the Flex Web Service uses a sliding-window rate limit, so every
    request during throttle pushes the reset further out and the daemon
    perpetuated its own throttle for ~24h on May 9 2026. See
    feedback_flex_cash_transaction_lag.md.

Throttle handling:
    Documented Flex throttle codes (1001 / 1018 / 1019) raise
    ``FlexThrottleError`` IMMEDIATELY — no internal retry, since each
    retry burns more of the sliding-window budget. The daemon handler
    intercepts the error and advances its circuit breaker (24h -> 48h
    -> 72h -> 168h capped) before the next attempt.

    Other transient failures (network blip, parse error) get exactly
    ONE bounded retry within the call.
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
    load_dotenv(_PROJECT_DIR / "web" / ".env")  # ← TURSO creds live here on Hetzner
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


# Documented throttle codes — every retry on these burns the sliding-window
# budget further. We do NOT retry internally; the daemon handler's circuit
# breaker waits 24h+ before the next attempt.
_FLEX_THROTTLE_CODES = {
    "1001",  # Statement could not be generated at this time.
    "1018",  # Too many requests have been made from this token.
    "1019",  # Statement generation in progress.
}

# Single bounded retry on a non-throttle transient (network blip, parse error).
_MAX_SOFT_RETRY_ATTEMPTS = 2  # initial + 1 retry


# Imported lazily to avoid a circular when the handler module imports this
# script's pure functions for testing without the full daemon scaffolding.
def _flex_throttle_error_cls():
    from monitor_daemon.handlers._throttle_backoff import FlexThrottleError
    return FlexThrottleError


class _FlexAppError(RuntimeError):
    """Flex returned a structured ErrorCode / ErrorMessage that isn't a
    throttle (auth, bad query id, etc.). NOT retryable — retrying won't
    flip a 1012 into a success.
    """


def _send_request_once(token: str, query_id: str) -> str:
    """One SendRequest hit. Returns the ReferenceCode or raises.

    Raises:
        FlexThrottleError    on documented throttle codes (1001/1018/1019)
        _FlexAppError        on any other structured Flex error
        Exception            on transport / parse failure
    """
    params = urlencode({"t": token, "q": query_id, "v": "3"})
    resp = urlopen(f"{_SEND_URL}?{params}", timeout=30)
    body = resp.read().decode("utf-8")
    root = ET.fromstring(body)
    ref_node = root.find(".//ReferenceCode")
    if ref_node is not None and ref_node.text:
        return ref_node.text

    code_node = root.find(".//ErrorCode")
    msg_node = root.find(".//ErrorMessage")
    code = (code_node.text or "").strip() if code_node is not None and code_node.text else ""
    message = (msg_node.text or "").strip() if msg_node is not None and msg_node.text else "no ErrorMessage from IBKR"
    detail = f"Flex SendRequest failed (code {code or 'N/A'}): {message}"

    if code in _FLEX_THROTTLE_CODES:
        raise _flex_throttle_error_cls()(code, detail)
    raise _FlexAppError(detail)


def _request_reference_code(token: str, query_id: str) -> str:
    """Call SendRequest with a single bounded retry on transport blips only.

    Throttle codes (1001/1018/1019) raise FlexThrottleError on the first
    hit — no retry, since every retry pushes the sliding-window out
    further. Structured Flex application errors (auth, bad query id)
    fail fast — retrying won't flip them.

    Only transport / parse failures (network blip, bad XML) get a single
    bounded retry; the daemon's daily window catches anything that takes
    longer to resolve.
    """
    last_transport_error: Optional[BaseException] = None
    for attempt in range(1, _MAX_SOFT_RETRY_ATTEMPTS + 1):
        try:
            return _send_request_once(token, query_id)
        except _flex_throttle_error_cls():
            raise
        except _FlexAppError:
            raise
        except Exception as exc:
            last_transport_error = exc
            if attempt >= _MAX_SOFT_RETRY_ATTEMPTS:
                raise
            time.sleep(1.0)
            continue
    # Defensive — loop above always returns or raises.
    if last_transport_error is not None:
        raise last_transport_error
    raise RuntimeError("Flex SendRequest failed: unknown error")


def fetch_cash_transactions(
    token: str,
    query_id: str,
    *,
    max_polls: int = 40,
    poll_sleep: float = 2.0,
    max_poll_sleep: float = 15.0,
) -> list[dict[str, Any]]:
    """Fetch the NAV Flex Query and parse CashTransaction rows.

    Polls with capped exponential backoff: 2s → 4s → 8s → 15s (capped),
    repeated up to `max_polls` times. Worst case total wait is roughly
    2 + 4 + 8 + 15*37 ≈ 9.5 minutes — long enough to ride out the
    ~17:00 ET EOD spike when Flex is serving thousands of statement
    requests at once. The previous fixed 20 × 3s = 60s budget was too
    tight; the 2026-05-14 incident was a perfectly transient Flex
    backend slowness that the script gave up on at exactly 60s.

    Returns a list of dicts ready to feed `upsert_cash_flow`.

    Raises:
        FlexThrottleError    on Flex throttle codes 1001 / 1018 / 1019.
                             Surfaced typed so the daemon handler can
                             advance its circuit breaker without retrying.
        RuntimeError         on auth failure, parse error, or polling
                             timeout (after one bounded soft retry).
    """
    ref_code = _request_reference_code(token, query_id)

    xml_text = ""
    sleep_s = poll_sleep
    for _ in range(max_polls):
        time.sleep(sleep_s)
        params2 = urlencode({"t": token, "q": ref_code, "v": "3"})
        resp2 = urlopen(f"{_GET_URL}?{params2}", timeout=30)
        xml_text = resp2.read().decode("utf-8")
        if "<FlexStatements" in xml_text:
            break
        sleep_s = min(sleep_s * 2, max_poll_sleep)
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
