"""The ET calendar day of an instant has one definition.

Earnings, catalysts, and informed flow used to carry the same helper.
A trading-day check is still market_calendar.is_trading_day_et.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER = "scripts/utils/market_calendar.py"
MARKER = "def _et_today(now: datetime) -> date:"


def test_et_today_helper_is_not_copied():
    found = []
    for path in (REPO_ROOT / "scripts").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("scripts/tests/") or "/tests/" in f"/{rel}":
            continue
        if MARKER in path.read_text(encoding="utf-8"):
            found.append(rel)
    assert found == []
    text = (REPO_ROOT / OWNER).read_text(encoding="utf-8")
    assert "def et_calendar_date(" in text


def test_et_calendar_date_crosses_midnight():
    from utils.market_calendar import et_calendar_date

    # 04:30 UTC on 2026-11-28 is 23:30 ET on 2026-11-27.
    instant = datetime(2026, 11, 28, 4, 30, tzinfo=timezone.utc)
    assert et_calendar_date(instant).isoformat() == "2026-11-27"
