"""Fail-closed Flex statement classifier. No I/O, no Flex Web Service.

Route by section presence. ``FlexStatement`` vs ``FlexQueryResponse`` is
not a discriminator. A file that looks like both Activity and Trade
Confirmation is Activity: ignore ``Trade`` nodes, never send them to
journal_rehydrate.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Any, Dict

ACTIVITY = "activity"
TRADES = "trades"

# The account this host trades. When set, a statement for any other account is
# refused rather than routed into cash_flow_sync and perf_twr_builder — section
# presence alone cannot tell the operator's daily from someone else's export,
# or from an accidentally re-downloaded 365-day file dropped in the inbox
# beside it. R-359.
_ACCOUNT_ENV = "IB_FLEX_ACCOUNT_ID"


class FlexClassifyError(ValueError):
    """Statement does not uniquely match 1442520 or 1422766."""


def statement_metadata(xml_text: str) -> Dict[str, Any]:
    """Account id and reporting period off the `FlexStatement` element.

    `0059_flex_deliveries.sql` reserves `period_from`/`period_to` for exactly
    this; nothing populated or consulted them until R-326/R-359.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FlexClassifyError(f"unreadable_xml:{exc}") from exc
    statement = root.find(".//FlexStatement")
    if statement is None:
        return {"account_id": None, "period_from": None, "period_to": None}
    return {
        "account_id": (statement.get("accountId") or "").strip() or None,
        "period_from": (statement.get("fromDate") or "").strip() or None,
        "period_to": (statement.get("toDate") or "").strip() or None,
    }


def classify_flex_xml(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FlexClassifyError(f"unreadable_xml:{exc}") from exc

    configured = (os.environ.get(_ACCOUNT_ENV) or "").strip()
    if configured:
        found = statement_metadata(xml_text)["account_id"]
        if found and found != configured:
            raise FlexClassifyError(
                f"account_mismatch: statement is for {found}, "
                f"this host trades {configured}"
            )

    has_nav = root.find(".//EquitySummaryByReportDateInBase") is not None
    # Section presence, not row presence: a Last Business Day file with no
    # ACATS still has <Transfers></Transfers>. Requiring a <Transfer> child
    # rejected every quiet session.
    has_cash = (
        root.find(".//CashTransactions") is not None
        or root.find(".//CashTransaction") is not None
    )
    has_transfer = root.find(".//Transfers") is not None
    has_trade = root.find(".//Trade") is not None

    if has_nav and has_cash and has_transfer:
        return ACTIVITY
    if has_trade and not has_nav:
        return TRADES
    raise FlexClassifyError(
        "ambiguous_or_incomplete:"
        f"nav={int(has_nav)} cash={int(has_cash)} "
        f"transfer={int(has_transfer)} trade={int(has_trade)}"
    )
