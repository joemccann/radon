#!/usr/bin/env python3
"""Cash-flow sync — pull deposits/withdrawals/dividends from IB Flex Query.

The existing `journal` table tracks executions only. Capital movements
(deposits, withdrawals, dividends paid, interest, fees) live in IB's
`CashTransaction` Flex section and aren't surfaced anywhere in radon yet.
This script bridges that gap.

Usage:
    python -m scripts.cash_flow_sync
    python -m scripts.cash_flow_sync --types Deposit,Withdrawal
    python -m scripts.cash_flow_sync --json          # print parsed rows, don't write

    # Operator recovery — no network, no credentials required:
    python -m scripts.cash_flow_sync --from-file ~/Downloads/statement.xml
    python -m scripts.cash_flow_sync --from-file statement.xml --dry-run
    python -m scripts.cash_flow_sync --from-file statement.xml --since 2026-06-01

Required env for a LIVE pull (already set on Hetzner per
/home/radon/radon-cloud/.env). `--from-file` needs neither:
    IB_FLEX_TOKEN          - Flex Web Service token
    IB_FLEX_NAV_QUERY_ID   - Flex Query ID with CashTransaction section enabled

Outputs:
    Turso `cash_flows` table (one row per transactionID, idempotent).
    `data/cash_flows.json`    - file fallback / debug trace of last pull.
    A machine-readable status line as the LAST line of stdout, e.g.
        {"status": "error", "class": "throttle", "code": "1001"}
    The daemon handler branches on the EXIT CODE (see below); the status
    line carries the detail.

Exit codes (the handler's classification contract — never classify a
failure by substring-matching stderr again):
     0  success
     1  configuration error (missing token / query id / unreadable file)
    10  Flex rate limit (code 1018 ONLY) — breaker ladder
    11  permanent Flex application error (auth, unknown query id, unknown
        error code). Retrying cannot flip it; do not spend more requests.
    15  Flex lockout (code 1025). Undocumented failed-attempts lockout.
        Retrying extends it. Shared token embargo, not the next 08:00 window.
    12  statement never became ready inside the poll budget, or a transport
        failure. Soft lane.
    13  the statement came back but would not parse. Soft lane.
    14  the WRITE failed. The fetch already succeeded — a retry must replay
        the statement, never spend another SendRequest.

Cadence:
    monitor_daemon `cash_flow_sync` handler runs this once per ET trading
    day at 08:00 ET (pre-open). IBKR Flex publishes cash
    transactions once per day with a ~1-day settlement lag — a single
    well-timed daily call after the publication window is sufficient.

    The 4h cadence used through 2026-05-08 fired up to 12 attempts per
    day; the Flex Web Service uses a sliding-window rate limit, so every
    request during throttle pushes the reset further out and the daemon
    perpetuated its own throttle for ~24h on May 9 2026. See
    feedback_flex_cash_transaction_lag.md.

Throttle handling:
    The one documented rate-limit code (1018) raises
    ``FlexThrottleError`` IMMEDIATELY — no internal retry, since each
    retry burns more of the sliding-window budget. Both legs are checked:
    a throttle returned on a GetStatement POLL used to be indistinguishable
    from "not ready", so a throttled token drove 30+ further requests at
    speed while already throttled.

    Other transient failures (network blip, parse error) get exactly
    ONE bounded retry within the call.
"""

from __future__ import annotations

import argparse
import hashlib
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
# (_classify, _normalize_date, parse_cash_transactions, describe_statement_shape,
# fetch_cash_transactions) can be unit-tested without libsql_experimental
# installed in the test environment.

# Flex Web Service endpoints
_SEND_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
_GET_URL = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"


# ── Exit codes ─────────────────────────────────────────────────────────
# One code per failure CLASS. The daemon handler branches on these; the
# previous contract was a substring search over the last three lines of
# stderr, which silently routed permanent errors into the retry lane.
EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_THROTTLE = 10
EXIT_FLEX_APP_ERROR = 11
EXIT_STATEMENT_NOT_READY = 12
EXIT_PARSE_ERROR = 13
EXIT_WRITE_ERROR = 14
EXIT_FLEX_LOCKOUT = 15
# R-100: a pre-flight embargo short-circuit performs NO HTTP, so it is not
# evidence of a fresh IBKR 1025 and must never re-arm the token-wide
# deadline. It exited 15 like a real lockout, and the daemon handler mapped
# 15 straight back to record_lockout — every arming path extended the outage.
EXIT_FLEX_PREFLIGHT_EMBARGO = 16
# A run with no statement source did no work. Distinct from EXIT_OK so the
# 25h cash-flow-sync freshness window is not held green by a nightly no-op,
# and distinct from the error codes because nothing failed. R-328.
EXIT_FLEX_SEND_DISABLED = 17


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


# IBKR's error taxonomy, as published:
#   https://www.ibkrguides.com/clientportal/performanceandstatements/flex3error.htm
#
#   1001  Statement could not be generated at this time. Please try again shortly.
#   1009  The server is under heavy load. Statement could not be generated at
#         this time. Please try again shortly.
#   1018  Too many requests have been made from this token. Please try again
#         shortly.  Limited to one request per second, 10 requests per minute
#         (per token).
#   1019  Statement generation in progress. Please try again shortly.
#
# ONE of these is a rate limit. This set previously held 1001, 1018 AND 1019,
# and every one of them raised FlexThrottleError and escalated the local
# 24h/48h/72h/168h ladder. 1019 is the ordinary NOT-READY response during
# polling, so "still generating" bought a 24-hour backoff; 1001 is a transient
# generation failure that IBKR tells you to retry shortly, and it escalated
# toward a one-week embargo. That is the most plausible root cause of the
# 10-day outage from 2026-08-06.
#
# Note also that IBKR publishes NO daily or multi-day cooldown. The documented
# limit is 10 requests per minute and it clears in a minute; the ladder is
# Radon-local policy and is far more conservative than the limit it models.
_FLEX_THROTTLE_CODES = {
    "1018",  # the only documented rate limit
}

# Undocumented. Not in IBKR's published v3 table (ends 1021). Observed as
# "Too many failed attempts. Please review your configuration." Earned by
# retrying 1001 against the same token; every further SendRequest extends it.
_FLEX_LOCKOUT_CODES = {
    "1025",
}

# "Try again shortly" — transient server-side generation failures. These take
# the SOFT lane (short bounded retry), never the breaker ladder.
_FLEX_TRANSIENT_CODES = {
    "1001",  # Statement could not be generated at this time.
    "1009",  # The server is under heavy load.
}

# Not an error at all: the statement is still being built. The poll loop must
# keep waiting rather than treating it as a failure of any kind.
_FLEX_NOT_READY_CODES = {
    "1019",  # Statement generation in progress.
}

# Single bounded retry on a non-throttle transient (network blip, parse error).
_MAX_SOFT_RETRY_ATTEMPTS = 2  # initial + 1 retry

# ── Poll budget: ONE number, ONE owner ─────────────────────────────────
# The script's wall budget for waiting on statement generation. Every
# other deadline in the chain is DERIVED from it, outermost last:
#
#   FLEX_POLL_BUDGET_SECONDS (here)
#     < handler subprocess timeout (budget + margin)
#       < CashFlowSyncHandler.max_runtime_seconds (the daemon deadline)
#
# Before 2026-08-16 those three were 569s / 180s / 120s, in that order:
# the daemon killed the handler before the handler killed the subprocess
# before the subprocess finished its FIRST HALF of polling. 14 of the 16
# recorded failure transitions were that self-inflicted kill, each one
# spending a SendRequest and writing zero rows.
#
# 420s covers the 2.5-3.5 minute statement-generation latency IBKR shows
# around Flex generation spikes with room to spare.
FLEX_POLL_BUDGET_SECONDS = 420.0
INITIAL_POLL_SLEEP_SECONDS = 2.0
MAX_POLL_SLEEP_SECONDS = 15.0

# Belt-and-braces bound so a pathological (initial, cap) pair can never
# produce an unbounded poll list.
_ABSOLUTE_MAX_POLLS = 200


def poll_delays(
    budget_seconds: float,
    *,
    initial: float = INITIAL_POLL_SLEEP_SECONDS,
    cap: float = MAX_POLL_SLEEP_SECONDS,
    max_polls: Optional[int] = None,
) -> list[float]:
    """Capped-exponential sleep schedule that fits inside `budget_seconds`.

    2s → 4s → 8s → 15s → 15s … Returns the delays, so the poll count is a
    consequence of the budget rather than a second number to keep in sync.
    """
    hard_cap = _ABSOLUTE_MAX_POLLS if max_polls is None else max(int(max_polls), 0)
    delays: list[float] = []
    total = 0.0
    sleep_s = max(float(initial), 0.0)
    while len(delays) < hard_cap:
        if sleep_s > 0 and total + sleep_s > budget_seconds:
            break
        delays.append(sleep_s)
        total += sleep_s
        if sleep_s <= 0:
            if max_polls is None:
                break
            continue
        sleep_s = min(sleep_s * 2, cap)
    return delays


# Imported lazily to avoid a circular when the handler module imports this
# script's pure functions for testing without the full daemon scaffolding.
def _flex_throttle_error_cls():
    from monitor_daemon.handlers._throttle_backoff import FlexThrottleError
    return FlexThrottleError


class _FlexLockoutError(RuntimeError):
    """Flex 1025: failed-attempts lockout. Not a published config error.

    1014 is "Query is invalid", 1012 is "Token has expired". 1025 is what
    IBKR returns after too many failed generation attempts on a still-valid
    token. Retrying it is how the cash-flow lozenge stays red for days.
    """

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class _FlexAppError(RuntimeError):
    """Flex returned a structured ErrorCode / ErrorMessage that isn't a
    throttle (auth, bad query id, etc.). NOT retryable — retrying won't
    flip a 1012 into a success.
    """


class _FlexTransientError(RuntimeError):
    """IBKR could not generate the statement right now and said to retry.

    Codes 1001 and 1009. Distinct from `_FlexAppError` because retrying DOES
    flip these, and distinct from `FlexThrottleError` because they are not a
    rate limit and must never escalate the breaker ladder. Conflating the three
    is what turned "try again shortly" into a multi-day embargo.
    """


class _StatementNotReady(RuntimeError):
    """The poll budget expired before IBKR finished generating the
    statement. Retryable, but not today's problem — the next daily window
    will ask again."""


class _ConfigError(RuntimeError):
    """Missing credentials, or a `--from-file` path we cannot read. A
    human has to act; no amount of retrying helps, and no Flex request
    was or will be spent."""


def _flex_error_from(root: ET.Element, leg: str) -> Optional[BaseException]:
    """Turn a Flex `<ErrorCode>` body into the right typed exception, or None.

    Applied to BOTH legs. A 1018 on a GetStatement poll used to be
    indistinguishable from "not ready", so an already-throttled token kept
    firing polls at full speed for the rest of the budget. That is still
    handled -- 1018 raises on either leg.

    The inverse mistake is the one this function now avoids: 1019 IS "not
    ready". Returning any exception for it aborts a poll loop that should
    simply keep waiting, and classifying it as a throttle bought a 24-hour
    backoff for a statement that was seconds from being ready.
    """
    code_node = root.find(".//ErrorCode")
    if code_node is None or not (code_node.text or "").strip():
        return None
    code = (code_node.text or "").strip()
    if code in _FLEX_NOT_READY_CODES and leg == "GetStatement":
        # The statement is still being built. Not an error on the poll leg --
        # returning one would abort a loop whose whole job is to wait.
        return None
    msg_node = root.find(".//ErrorMessage")
    message = (
        (msg_node.text or "").strip()
        if msg_node is not None and msg_node.text
        else "no ErrorMessage from IBKR"
    )
    detail = f"Flex {leg} failed (code {code}): {message}"
    if code in _FLEX_LOCKOUT_CODES:
        return _FlexLockoutError(code, detail)
    if code in _FLEX_THROTTLE_CODES:
        return _flex_throttle_error_cls()(code, detail)
    if code in _FLEX_TRANSIENT_CODES or code in _FLEX_NOT_READY_CODES:
        # 1019 reaching this line means it came back on SendRequest, i.e. a
        # generation is already in flight. Retryable, not permanent -- and
        # emphatically not the "no ReferenceCode" hard error it used to fall
        # through to, which is classified as never-retry.
        return _FlexTransientError(detail)
    return _FlexAppError(detail)


def _send_request_once(token: str, query_id: str) -> str:
    """One SendRequest hit. Returns the ReferenceCode or raises.

    Raises:
        FlexThrottleError    on the rate-limit code (1018)
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

    error = _flex_error_from(root, "SendRequest")
    if error is not None:
        raise error
    raise _FlexAppError("Flex SendRequest failed: no ReferenceCode and no ErrorCode")


def _request_reference_code(token: str, query_id: str) -> str:
    """Call SendRequest with a single bounded retry on transport blips only.

    The rate-limit code (1018) raises FlexThrottleError on the first
    hit — no retry, since every retry pushes the sliding-window out
    further. Structured Flex application errors (auth, bad query id)
    fail fast — retrying won't flip them. Transient generation failures
    (1001/1009) and lockout (1025) also fail fast: a second SendRequest
    one second later is how 1001 becomes 1025.

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
        except _FlexLockoutError:
            raise
        except _FlexAppError:
            raise
        except _FlexTransientError:
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


def parse_cash_transactions(xml_text: str) -> list[dict[str, Any]]:
    """Parse CashTransaction rows out of a Flex statement. No I/O.

    Pure counterpart of `fetch_cash_transactions` — same rows, no network.
    Lets a saved statement be replayed after a throttle embargo, and makes
    the parse rules testable without ever hitting the Flex Web Service.

    Two CashTransaction rows are dropped:
      * no `transactionID` — IBKR emits per-day aggregate rows alongside the
        detail rows they summarize (2026-06-24 carries -42,000 and -60,000
        details plus a -102,000 aggregate). Keeping them double-counts.
      * `amount` of exactly zero — nothing moved.

    Cash `<Transfer>` rows (`assetCategory=CASH`, non-zero `cashTransfer`)
    become Deposit/Withdrawal. Securities ACATS with cash 0 are not cash
    flows (TWR already counts them as external flow).

    Rows sharing a `transactionID` are all kept. The first keeps that id;
    later ones get `{transactionID}#{n}` so upsert cannot last-write-wins
    the extra amounts (2026-07-06 interest trio: $38.18).

    Returns a list of dicts ready to feed `upsert_cash_flow_rows`.
    """
    out: list[dict[str, Any]] = []
    root = ET.fromstring(xml_text)
    # First pass: which transactionIDs are carried by more than one row. Only
    # those need disambiguating, and knowing it up front keeps the id a pure
    # function of the row's own content rather than of its position. R-329.
    counts: dict[str, int] = {}
    for ct in root.findall(".//CashTransaction"):
        tid = (ct.get("transactionID") or "").strip()
        if tid and float(ct.get("amount") or 0.0) != 0.0:
            counts[tid] = counts.get(tid, 0) + 1
    for node in root.findall(".//Transfer"):
        tid = _cash_transfer_id_and_amount(node)[0]
        if tid:
            counts[tid] = counts.get(tid, 0) + 1
    duplicated = {tid for tid, n in counts.items() if n > 1}
    for ct in root.findall(".//CashTransaction"):
        txn_id = (ct.get("transactionID") or "").strip()
        if not txn_id:
            continue
        amt = float(ct.get("amount") or 0.0)
        if amt == 0.0:
            continue
        raw_type = (ct.get("type") or "").strip()
        date_str = _normalize_date(ct.get("reportDate") or ct.get("dateTime") or "")
        description = (ct.get("description") or "").strip() or None
        # R-329: the suffix used to be the row's DOCUMENT POSITION
        # (`txn_id if occurrence == 0 else f"{txn_id}#{occurrence}"`), so the
        # id of a given economic row moved whenever IBKR reissued the
        # statement with a sibling dropped, reordered, or a row inserted
        # ahead. `upsert_cash_flow_rows` is insert/update-only with no delete
        # pass, so the stale id survived as a phantom row and the cash-flow
        # total was overstated. Keying the suffix on the row's own CONTENT
        # makes the same economics map to the same id every time.
        unique_id = _disambiguated_id(
            txn_id, raw_type, amt, date_str, description, duplicated
        )
        out.append({
            "id": unique_id,
            "date": date_str,
            "type": _classify(raw_type, amt),
            "amount": amt,
            "currency": (ct.get("currency") or "USD").upper(),
            "description": description,
            "raw_type": raw_type or None,
        })
    for node in root.findall(".//Transfer"):
        row = _cash_transfer_row(node, duplicated)
        if row is not None:
            out.append(row)
    return out


def _disambiguated_id(
    txn_id: str,
    raw_type: str,
    amount: float,
    date_str: str,
    description: str | None,
    duplicated: set[str],
) -> str:
    """A stable id for one of several rows sharing a `transactionID`.

    IBKR really does issue one transactionID per posting batch with one row
    per sub-category, so the id alone is not unique. A row whose id appears
    ONCE in the statement keeps the raw IBKR id — the overwhelmingly common
    case, and changing it would rewrite every existing key for nothing.

    Every row of a duplicated id is suffixed with a short hash of its own
    content, INCLUDING the first: "first keeps the raw id" is still an ordinal
    rule, so a row inserted ahead of the trio would take the unsuffixed id and
    push the original onto a hash. Suffixing all of them makes the mapping a
    pure function of the row's economics, independent of how many siblings
    there are or what order they arrive in. R-329.

    Membership of `duplicated` is computed in a first pass over the whole
    document, so it cannot depend on position either.
    """
    if txn_id not in duplicated:
        return txn_id
    fingerprint = "|".join((raw_type, f"{amount!r}", date_str, description or ""))
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]
    return f"{txn_id}#{digest}"


def _cash_transfer_id_and_amount(node: ET.Element) -> tuple[str, float]:
    if (node.get("assetCategory") or "").strip().upper() != "CASH":
        return "", 0.0
    txn_id = (node.get("transactionID") or "").strip()
    try:
        amt = float((node.get("cashTransfer") or "0").replace(",", ""))
    except ValueError:
        return "", 0.0
    if not txn_id or amt == 0.0:
        return "", 0.0
    return txn_id, amt


def _cash_transfer_row(node: ET.Element, duplicated: set[str]) -> Optional[dict[str, Any]]:
    """Cash ACATS / wires on <Transfer>. Securities ACATS (cash 0) stay out."""
    txn_id, amt = _cash_transfer_id_and_amount(node)
    if not txn_id:
        return None
    desc = (node.get("description") or "").strip()
    if desc in ("", "--"):
        desc = (node.get("type") or "ACATS").strip() or "ACATS"
    xfer_type = (node.get("type") or "ACATS").strip() or "ACATS"
    direction = (node.get("direction") or "").strip().upper()
    raw_type = f"Transfer:{xfer_type}:{direction}" if direction else f"Transfer:{xfer_type}"
    date_str = _normalize_date(node.get("reportDate") or node.get("dateTime") or "")
    return {
        "id": _disambiguated_id(txn_id, raw_type, amt, date_str, desc, duplicated),
        "date": date_str,
        "type": "Deposit" if amt > 0 else "Withdrawal",
        "amount": amt,
        "currency": (node.get("currency") or "USD").upper(),
        "description": desc,
        "raw_type": raw_type,
    }


def describe_statement_shape(xml_text: str) -> dict[str, Any]:
    """Structural fingerprint of a Flex statement. No I/O, no side effects.

    Whether `<Transfers>` or `levelOfDetail` appear at all is a checkbox in
    the IBKR query builder, and the difference silently halves or doubles
    every downstream total: over the legacy 365-day shape the two other
    parsers in this repo double-count every external flow exactly 2x
    (because they keep the id-less aggregate rows this one drops), and
    over a DETAIL-only export that 2x vanishes. Recording the shape turns
    a silent config change into a visible one.

    It also counts what the parser threw away, so the id-less-row skip
    stops being an invisible proxy for `levelOfDetail == "SUMMARY"`.
    """
    root = ET.fromstring(xml_text)
    statements = root.findall(".//FlexStatement")
    cash = root.findall(".//CashTransaction")

    skipped_no_id = 0
    skipped_zero = 0
    parsed = 0
    currencies: set[str] = set()
    non_usd = 0
    levels: set[str] = set()
    for ct in cash:
        level = (ct.get("levelOfDetail") or "").strip()
        if level:
            levels.add(level)
        if not (ct.get("transactionID") or "").strip():
            skipped_no_id += 1
            continue
        if float(ct.get("amount") or 0.0) == 0.0:
            skipped_zero += 1
            continue
        parsed += 1
        currency = (ct.get("currency") or "USD").upper()
        currencies.add(currency)
        if currency != "USD":
            non_usd += 1

    first = statements[0] if statements else None
    return {
        "flex_statement_count": len(statements),
        "account_ids": sorted(
            {(s.get("accountId") or "").strip() for s in statements if s.get("accountId")}
        ),
        "period_from": _normalize_date(first.get("fromDate") or "") if first is not None else "",
        "period_to": _normalize_date(first.get("toDate") or "") if first is not None else "",
        "when_generated": (first.get("whenGenerated") or "") if first is not None else "",
        "cash_transaction_count": len(cash),
        "skipped_no_transaction_id": skipped_no_id,
        "skipped_zero_amount": skipped_zero,
        "parsed_rows": parsed,
        "levels_of_detail": sorted(levels),
        "has_transfers_section": root.find(".//Transfers") is not None,
        "transfer_count": len(root.findall(".//Transfer")),
        "currencies": sorted(currencies),
        "non_usd_rows": non_usd,
    }


def statement_shape_warnings(shape: dict[str, Any]) -> list[str]:
    """Human-readable drift notes. Non-fatal by design.

    A single new IBKR quirk taking down the nightly sync is the exact harm
    this overhaul exists to reduce, so shape drift reports and continues.
    """
    warnings: list[str] = []
    if shape["flex_statement_count"] != 1:
        warnings.append(
            f"expected exactly 1 FlexStatement, found {shape['flex_statement_count']}"
        )
    if len(shape["account_ids"]) > 1:
        warnings.append(f"multiple accountIds in one statement: {shape['account_ids']}")
    if not shape["levels_of_detail"]:
        warnings.append(
            "no levelOfDetail attribute on any CashTransaction — the "
            f"{shape['skipped_no_transaction_id']} row(s) skipped for a blank "
            "transactionID are being treated as IBKR aggregates by proxy"
        )
    if shape["has_transfers_section"]:
        warnings.append(
            f"statement carries {shape['transfer_count']} <Transfer> row(s); "
            "cash ACATS are cash_flows, securities ACATS are TWR-only"
        )
    if shape["non_usd_rows"]:
        others = [c for c in shape["currencies"] if c != "USD"]
        warnings.append(
            f"{shape['non_usd_rows']} non-USD row(s) ({', '.join(others)}) — amounts "
            "are transaction-currency and must not be summed with USD rows"
        )
    return warnings


def fetch_statement_xml(
    token: str,
    query_id: str,
    *,
    max_polls: Optional[int] = None,
    poll_sleep: float = INITIAL_POLL_SLEEP_SECONDS,
    max_poll_sleep: float = MAX_POLL_SLEEP_SECONDS,
    budget_seconds: Optional[float] = None,
) -> str:
    """Fetch the raw Flex statement XML. One SendRequest, bounded polling.

    Raises:
        FlexThrottleError    on the Flex rate-limit code 1018,
                             from EITHER leg.
        _FlexLockoutError    on undocumented 1025 (failed-attempts lockout).
        _FlexAppError        on any other structured Flex error.
        _StatementNotReady   when the poll budget expires.
        FlexTokenLocked      when a prior 1025 embargo is still live.
    """
    _raise_if_token_locked()
    ref_code = _request_reference_code(token, query_id)

    budget = FLEX_POLL_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    delays = poll_delays(
        budget, initial=poll_sleep, cap=max_poll_sleep, max_polls=max_polls
    )
    # R-103: the schedule sizes SLEEP to the budget, but the urlopen in the
    # same loop body was charged nowhere — worst case 30 x 30 + 420 = 1320s
    # against a 480s SIGKILL, and even a benign 3s GetStatement latency put
    # the run past it. HTTP time is now MEASURED and charged against the same
    # budget, and each read is bounded by what is left of it.
    elapsed = 0.0
    polls = 0
    for sleep_s in delays:
        if elapsed + sleep_s > budget:
            break
        time.sleep(sleep_s)
        elapsed += sleep_s
        remaining = budget - elapsed
        if remaining <= 0:
            break
        polls += 1
        params2 = urlencode({"t": token, "q": ref_code, "v": "3"})
        started = time.monotonic()
        resp2 = urlopen(f"{_GET_URL}?{params2}", timeout=min(30.0, remaining))
        xml_text = resp2.read().decode("utf-8")
        elapsed += max(0.0, time.monotonic() - started)
        if "<FlexStatements" in xml_text:
            return xml_text
        try:
            error = _flex_error_from(ET.fromstring(xml_text), "GetStatement")
        except ET.ParseError:
            error = None
        if error is not None:
            raise error

    raise _StatementNotReady(
        f"Flex statement not ready after {polls} polls "
        f"({budget:.0f}s budget)"
    )


def fetch_cash_transactions(
    token: str,
    query_id: str,
    *,
    max_polls: Optional[int] = None,
    poll_sleep: float = INITIAL_POLL_SLEEP_SECONDS,
    max_poll_sleep: float = MAX_POLL_SLEEP_SECONDS,
    budget_seconds: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Fetch the NAV Flex Query and parse CashTransaction rows."""
    return parse_cash_transactions(
        fetch_statement_xml(
            token,
            query_id,
            max_polls=max_polls,
            poll_sleep=poll_sleep,
            max_poll_sleep=max_poll_sleep,
            budget_seconds=budget_seconds,
        )
    )


def _filter_types(rows: list[dict[str, Any]], allowed: Optional[set[str]]) -> list[dict[str, Any]]:
    if not allowed:
        return rows
    return [r for r in rows if r["type"] in allowed]


def _filter_since(rows: list[dict[str, Any]], since: Optional[str]) -> list[dict[str, Any]]:
    if not since:
        return rows
    return [r for r in rows if r["date"] >= since]


def _load_existing_cash_flow_ids() -> set[str]:
    """transactionIDs already stored, for the `--dry-run` diff.

    Turso-first per the persistence rule. Read-only, bounded, and every
    caller treats a failure as "no baseline" rather than an error.
    """
    from db.hrana_http import hrana_query  # noqa: PLC0415 — lazy; libsql optional

    rows = hrana_query("SELECT id FROM cash_flows LIMIT 20000", ())
    ids: set[str] = set()
    for row in rows or []:
        value = row.get("id") if isinstance(row, dict) else row[0]
        if value is not None:
            ids.add(str(value))
    return ids


def _emit_status(status: str, failure_class: str, **extra: Any) -> None:
    """Machine-readable last line of stdout. The handler reads this for
    detail; it branches on the exit code."""
    payload: dict[str, Any] = {"status": status, "class": failure_class}
    payload.update(extra)
    print(json.dumps(payload))


def _read_statement_file(path_str: str) -> str:
    path = Path(path_str).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _ConfigError(f"cannot read statement file {path}: {exc}") from exc


def _raise_if_token_locked() -> None:
    """Skip SendRequest when a prior 1025 embargo is still live."""
    try:
        from utils.flex_embargo import raise_if_blocked
    except Exception:
        return
    raise_if_blocked()


def _record_token_lockout(code: str) -> bool:
    """Arm the token-wide embargo. True iff at least one sink recorded it."""
    try:
        from utils.flex_embargo import record_lockout
        record_lockout(code)
    except Exception as exc:
        print(f"WARN: Flex lockout NOT recorded ({exc})", file=sys.stderr)
        return False
    return True


def _fetch_live_statement(*, sendrequest: bool = False) -> str:
    from utils.flex_send import assert_sendrequest_permitted

    token = os.environ.get("IB_FLEX_TOKEN")
    query_id = os.environ.get("IB_FLEX_NAV_QUERY_ID")
    if not token or not query_id:
        raise _ConfigError("IB_FLEX_TOKEN / IB_FLEX_NAV_QUERY_ID not configured")
    assert_sendrequest_permitted(allowed=sendrequest)
    return fetch_statement_xml(token, query_id)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync IB cash transactions to radon's cash_flows table"
    )
    parser.add_argument(
        "--types",
        default="",
        help="Comma-separated normalized types to keep (Deposit,Withdrawal,Dividend,Interest,Fee,WithholdingTax,Other). Default: all.",
    )
    parser.add_argument("--json", action="store_true", help="Print parsed rows as JSON to stdout, do NOT write to DB.")
    parser.add_argument("--no-file", action="store_true", help="Skip writing data/cash_flows.json")
    parser.add_argument(
        "--from-file",
        metavar="PATH",
        help="Parse a saved Flex statement instead of fetching. Makes NO network "
             "call and needs no credentials — the recovery path during a throttle "
             "embargo.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse, diff against the stored rows, print what would change, write nothing.",
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Only keep rows dated on or after this date.",
    )
    parser.add_argument(
        "--sendrequest",
        action="store_true",
        help="Permit a live Flex SendRequest (Sunday recon, after embargo).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    # ── Acquire the statement ──────────────────────────────────────────
    try:
        if args.from_file:
            xml_text = _read_statement_file(args.from_file)
            source = args.from_file
        else:
            xml_text = _fetch_live_statement(sendrequest=args.sendrequest)
            source = "flex"
    except _ConfigError as exc:
        print(f"ERR: {exc}", file=sys.stderr)
        _emit_status("error", "config_error", message=str(exc))
        return EXIT_CONFIG_ERROR
    except _FlexLockoutError as exc:
        print(f"ERR: {exc}", file=sys.stderr)
        recorded = bool(_record_token_lockout(getattr(exc, "code", "1025")))
        _emit_status("error", "lockout", code=getattr(exc, "code", "1025"),
                     message=str(exc), embargo_recorded=recorded)
        return EXIT_FLEX_LOCKOUT
    except _FlexAppError as exc:
        print(f"ERR: {exc}", file=sys.stderr)
        _emit_status("error", "flex_app_error", message=str(exc))
        return EXIT_FLEX_APP_ERROR
    except _StatementNotReady as exc:
        print(f"ERR: {exc}", file=sys.stderr)
        _emit_status("error", "not_ready", message=str(exc))
        return EXIT_STATEMENT_NOT_READY
    except Exception as exc:
        throttle_cls = _flex_throttle_error_cls()
        if isinstance(exc, throttle_cls):
            print(f"ERR: {exc}", file=sys.stderr)
            _emit_status("error", "throttle", code=getattr(exc, "code", None),
                         message=str(exc))
            return EXIT_THROTTLE
        if type(exc).__name__ == "FlexTokenLocked":
            print(f"ERR: {exc}", file=sys.stderr)
            _emit_status("error", "preflight_embargo", code="1025", message=str(exc))
            return EXIT_FLEX_PREFLIGHT_EMBARGO
        if type(exc).__name__ == "FlexSendDisabled":
            # NOT `ok`, and NOT EXIT_OK. The scheduled daily unit passes
            # neither --from-file nor --sendrequest, so this branch runs every
            # night; reporting healthy meant the 25h `cash-flow-sync`
            # staleness window could never fire — the exact stale-data
            # condition it exists to catch. R-328.
            print(f"SKIP: {exc}", file=sys.stderr)
            _emit_status("skipped", "file_ingest_only", message=str(exc))
            return EXIT_FLEX_SEND_DISABLED
        print(f"ERR: cash flow fetch failed: {exc}", file=sys.stderr)
        _emit_status("error", "transport", message=str(exc))
        return EXIT_STATEMENT_NOT_READY

    # ── Parse ──────────────────────────────────────────────────────────
    try:
        rows = parse_cash_transactions(xml_text)
        shape = describe_statement_shape(xml_text)
    except Exception as exc:
        print(f"ERR: cash flow parse failed: {exc}", file=sys.stderr)
        _emit_status("error", "parse_error", message=str(exc), source=source)
        return EXIT_PARSE_ERROR

    warnings = statement_shape_warnings(shape)
    for warning in warnings:
        print(f"WARN: statement shape: {warning}", file=sys.stderr)

    allowed = {t.strip() for t in args.types.split(",") if t.strip()} or None
    rows = _filter_since(_filter_types(rows, allowed), args.since)
    rows.sort(key=lambda r: (r["date"], r["id"]))

    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        print()
        return EXIT_OK

    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1

    if args.dry_run:
        try:
            known = _load_existing_cash_flow_ids()
            new_rows: Optional[int] = len({r["id"] for r in rows} - known)
        except Exception as exc:  # noqa: BLE001 — a dry run must never fail on the baseline
            print(f"WARN: could not read the stored baseline: {exc}", file=sys.stderr)
            new_rows = None
        print(
            f"DRY RUN: {len(rows)} rows parsed from {source}, "
            f"{'unknown' if new_rows is None else new_rows} new. Breakdown: {by_type}"
        )
        _emit_status(
            "ok", "ok", dry_run=True, rows=len(rows), new_rows=new_rows,
            source=source, shape=shape, shape_warnings=warnings,
        )
        return EXIT_OK

    # ── Write ──────────────────────────────────────────────────────────
    # A write failure must NEVER be laundered into another Flex request:
    # the fetch already succeeded, so the retry replays the statement.
    try:
        from db.writer import upsert_cash_flow_rows
        from utils.atomic_io import atomic_save

        dropped_duplicate_ids = upsert_cash_flow_rows(rows)
    except Exception as exc:
        print(f"ERR: cash flow write failed: {exc}", file=sys.stderr)
        _emit_status("error", "write_error", message=str(exc), rows=len(rows),
                     source=source)
        return EXIT_WRITE_ERROR

    if dropped_duplicate_ids:
        # Plan item C12 — IBKR issues one transactionID per posting batch
        # and one row per sub-category, but `cash_flows.id` is the primary
        # key, so the batch collapses to its last row. Counting it here is
        # not a fix; it stops the loss being invisible.
        print(
            f"WARN: {dropped_duplicate_ids} row(s) shared a transactionID with a "
            "later row and were overwritten (cash_flows.id is the primary key)",
            file=sys.stderr,
        )

    if not args.no_file:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "synced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "count": len(rows),
            "rows": rows,
        }
        atomic_save(_DATA_DIR / "cash_flows.json", snapshot)

    print(f"Synced {len(rows)} cash flows. Breakdown: {by_type}")
    _emit_status(
        "ok", "ok", rows=len(rows), source=source, shape=shape,
        shape_warnings=warnings, overwritten_duplicate_ids=dropped_duplicate_ids,
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
