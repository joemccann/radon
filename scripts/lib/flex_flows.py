"""The single external-flow classifier and the Flex flow parser.

Classification is an exact-match allowlist on the normalized IBKR `type` field.
Substring matching is banned: it cannot tell a `type` attribute from free text
and it fails OPEN, which is how "Deposit Advance Reversal" became a deposit.

`is_external_flow_type` is defined in terms of `classify_flow_type`, and
`parse_flows` calls `classify_flow_type` directly, so there is exactly one
predicate in the repository.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Dict, Mapping, Optional, Tuple

from scripts.lib.twr_math import FlowClass, FlowSet, FlowsStatus, UnknownFlowType

EXTERNAL_FLOW_TYPES = frozenset(
    {
        "deposits/withdrawals",
        "deposits & withdrawals",
        "deposits and withdrawals",
        "internal transfers",
        "internal transfer",
    }
)

INTERNAL_FLOW_TYPES = frozenset(
    {
        "dividends",
        "payment in lieu of dividends",
        "withholding tax",
        "broker interest paid",
        "broker interest received",
        "broker fees",
        "bank interest paid",
        "bank interest received",
        "bond interest paid",
        "bond interest received",
        "other fees",
        "advisor fees",
        "commission adjustments",
        "price adjustments",
        "cash fx translation gain/loss",
        "detail",
    }
)

FLOWS_SOURCE = "flex_cash_transactions+transfers"

_CASH_CATEGORY = "CASH"
_DIRECTION_IN = "IN"
_FLOW_SECTIONS = ("CashTransactions", "Transfers")


def normalize_flow_type(raw: Optional[str]) -> str:
    return " ".join((raw or "").split()).strip().casefold()


def classify_flow_type(raw: Optional[str]) -> FlowClass:
    normalized = normalize_flow_type(raw)
    if normalized in EXTERNAL_FLOW_TYPES:
        return FlowClass.EXTERNAL
    if normalized in INTERNAL_FLOW_TYPES:
        return FlowClass.INTERNAL
    raise UnknownFlowType(f"unrecognized IBKR flow type: {raw!r}")


def is_external_flow_type(raw: Optional[str]) -> bool:
    return classify_flow_type(raw) is FlowClass.EXTERNAL


def _normalize_date(raw: Optional[str]) -> Optional[str]:
    text = (raw or "").strip()
    if not text:
        return None
    head = text.split(";")[0].split(" ")[0].split("T")[0]
    if len(head) == 8 and head.isdigit():
        return f"{head[:4]}-{head[4:6]}-{head[6:]}"
    if len(head) == 10 and head[4] == "-" and head[7] == "-":
        return head
    if len(head) == 10 and head[2] == "/" and head[5] == "/":
        month, day, year = head.split("/")
        return f"{year}-{month}-{day}"
    return None


def _amount(node: ET.Element, *names: str) -> Optional[float]:
    """First readable amount among `names`. IBKR groups thousands with commas."""
    for name in names:
        text = (node.get(name) or "").strip().replace(",", "")
        if not text:
            continue
        try:
            return float(text)
        except ValueError:
            continue
    return None


def _transfer_amount(node: ET.Element) -> float:
    """Signed base-currency value of a securities or cash transfer.

    `transferPrice` is a PER-SHARE price and is never read as an amount.

    The direction MULTIPLIES the reported sign, it does not replace it. IBKR's
    amount carries real economic sign inside a single transfer: a short option
    position transferred IN is a liability taken on (negative), and an ACATS
    can move cash OUT while the transfer as a whole is direction="IN". Taking
    abs() before applying the direction flipped both. On the operator's real
    2026-02-06 ACATS that inflated the flow from 655,497.16 to 1,282,260.84,
    and it turned a same-day cancel/rebook pair that must net to zero into a
    phantom +127,268.00 flow.

    All four quadrants are then correct:
      IN  + long  -> positive (value arrives)
      IN  + short -> negative (a liability arrives)
      OUT + long  -> negative (value leaves)
      OUT + short -> positive (a liability leaves)
    """
    direction = (node.get("direction") or "").strip().upper()
    if direction not in ("IN", "OUT"):
        raise UnknownFlowType(f"transfer:{node.get('type')!r}:no_direction")

    category = (node.get("assetCategory") or "").strip().upper()
    if category == _CASH_CATEGORY:
        magnitude = _amount(node, "cashTransfer")
    else:
        magnitude = _amount(node, "positionAmountInBase", "positionAmount")
    if magnitude is None:
        raise UnknownFlowType(f"transfer:{node.get('type')!r}:no_amount")

    return magnitude if direction == _DIRECTION_IN else -magnitude


def _missing_flow_sections(root: ET.Element) -> Tuple[str, ...]:
    """Flow sections this statement does not carry.

    Every missing section is a class of flow the statement cannot report. A
    CashTransactions-only query hides the entire `<Transfer>` class, which is
    where a securities transfer lives, so "no deposits" is unobservable from it.
    """
    return tuple(name for name in _FLOW_SECTIONS if root.find(f".//{name}") is None)


def parse_flows(xml_text: str, *, source: str = FLOWS_SOURCE) -> FlowSet:
    """Parse a Flex activity statement into net external flows per date.

    A statement that reports no external flow AND is missing a flow section is
    FAILED, not empty: a NAV-only query parses cleanly and yields zero flows,
    and that must never read as "this account has no deposits".
    """
    root = ET.fromstring(xml_text)
    missing = _missing_flow_sections(root)

    by_date: Dict[str, float] = {}
    external_rows = 0

    for node in root.findall(".//CashTransaction"):
        if classify_flow_type(node.get("type") or node.get("transactionType")) is not FlowClass.EXTERNAL:
            continue
        report_date = _normalize_date(
            node.get("reportDate") or node.get("dateTime") or node.get("date")
        )
        amount = _amount(node, "amount", "amountInBase", "amountInBaseCurrency")
        if report_date is None or amount is None:
            # Found and classified external, then unreadable. Dropping it turns
            # "we could not read this deposit" into "there are no deposits".
            stamp = node.get("reportDate") or node.get("dateTime") or "no_date"
            return FlowSet.failed(f"external_row_unparsable:{stamp}")
        by_date[report_date] = by_date.get(report_date, 0.0) + amount
        external_rows += 1

    for node in root.findall(".//Transfer"):
        amount = _transfer_amount(node)
        report_date = _normalize_date(
            node.get("reportDate") or node.get("dateTime") or node.get("date")
        )
        if report_date is None:
            raise UnknownFlowType(f"transfer:{node.get('type')!r}:no_date")
        by_date[report_date] = by_date.get(report_date, 0.0) + amount
        external_rows += 1

    if external_rows == 0:
        if missing:
            return FlowSet.failed(f"flows_section_absent:{','.join(missing)}")
        return FlowSet.empty_verified(source=source)
    return FlowSet(
        status=FlowsStatus.OK,
        by_date=by_date,
        source=source,
        missing_sections=missing,
    )


def flows_from_rows(rows: Mapping[str, float], *, source: str) -> FlowSet:
    """Wrap an already-classified date->amount mapping (Turso `cash_flows`)."""
    if not rows:
        return FlowSet.empty_verified(source=source)
    return FlowSet(status=FlowsStatus.OK, by_date=dict(rows), source=source)
