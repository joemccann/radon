"""REL-036 / R-062: shared UW budget counting for Next.js-originated fetches.

Six web route handlers fetch UW directly and used to increment nothing —
browsing-driven UW traffic was invisible to /uw/usage and the universe-scan
brake, so the fleet could pass the real 40k cap while the counter read low.
record_hits writes the same flock-shared budget file UWClient writes.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from utils.uw_budget import quota_date, record_hits, used  # noqa: E402

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=ET)
AT_RESET = datetime(2026, 8, 14, 20, 0, tzinfo=ET)


def test_record_hits_increments_by_count_in_one_call(tmp_path: Path) -> None:
    path = tmp_path / "uw_budget.json"
    assert record_hits(3, path=path, now=NOW) == 3
    assert record_hits(2, path=path, now=NOW) == 5
    payload = json.loads(path.read_text())
    assert payload["date"] == quota_date(NOW)
    assert payload["count"] == 5
    assert used(path=path, now=NOW) == 5


def test_record_hits_rolls_quota_day_at_2000_et(tmp_path: Path) -> None:
    path = tmp_path / "uw_budget.json"
    record_hits(7, path=path, now=NOW)
    assert record_hits(4, path=path, now=AT_RESET) == 4
    assert used(path=path, now=AT_RESET) == 4
