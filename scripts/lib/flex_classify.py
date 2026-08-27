"""Fail-closed Flex statement classifier. No I/O, no Flex Web Service.

Route by section presence. ``FlexStatement`` vs ``FlexQueryResponse`` is
not a discriminator. A file that looks like both Activity and Trade
Confirmation is Activity: ignore ``Trade`` nodes, never send them to
journal_rehydrate.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

ACTIVITY = "activity"
TRADES = "trades"


class FlexClassifyError(ValueError):
    """Statement does not uniquely match 1442520 or 1422766."""


def classify_flex_xml(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FlexClassifyError(f"unreadable_xml:{exc}") from exc

    has_nav = root.find(".//EquitySummaryByReportDateInBase") is not None
    has_cash = root.find(".//CashTransaction") is not None
    has_transfer = root.find(".//Transfer") is not None
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
