"""Process-wide UW daily budget (quota day resets 20:00 ET)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from utils.uw_budget import (
    DAILY_LIMIT,
    UNIVERSE_BLOCK_AT,
    quota_date,
    record_hit,
    remaining,
    should_block_universe_scan,
    used,
)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 14, 12, 0, tzinfo=ET)
BEFORE_RESET = datetime(2026, 8, 14, 19, 59, 59, tzinfo=ET)
AT_RESET = datetime(2026, 8, 14, 20, 0, tzinfo=ET)


def _seed(path: Path, count: int, now: datetime = NOW) -> None:
    path.write_text(json.dumps({"date": quota_date(now), "count": count}))


def test_record_hit_persists_date_and_count(tmp_path: Path) -> None:
    path = tmp_path / "uw_budget.json"
    assert record_hit(path=path, now=NOW) == 1
    assert record_hit(path=path, now=NOW) == 2
    payload = json.loads(path.read_text())
    assert payload == {"date": quota_date(NOW), "count": 2}
    assert used(path=path, now=NOW) == 2
    assert remaining(path=path, now=NOW) == DAILY_LIMIT - 2


def test_quota_day_resets_at_2000_et(tmp_path: Path) -> None:
    path = tmp_path / "uw_budget.json"
    record_hit(path=path, now=BEFORE_RESET)
    record_hit(path=path, now=BEFORE_RESET)
    assert used(path=path, now=BEFORE_RESET) == 2
    assert quota_date(BEFORE_RESET) != quota_date(AT_RESET)
    assert used(path=path, now=AT_RESET) == 0
    assert remaining(path=path, now=AT_RESET) == DAILY_LIMIT
    assert record_hit(path=path, now=AT_RESET) == 1
    payload = json.loads(path.read_text())
    assert payload == {"date": quota_date(AT_RESET), "count": 1}


def test_should_block_universe_scan_at_half_daily_cap(tmp_path: Path) -> None:
    path = tmp_path / "uw_budget.json"
    _seed(path, UNIVERSE_BLOCK_AT - 1)
    assert should_block_universe_scan(path=path, now=NOW) is False
    record_hit(path=path, now=NOW)
    assert used(path=path, now=NOW) == UNIVERSE_BLOCK_AT
    assert should_block_universe_scan(path=path, now=NOW) is True
    assert remaining(path=path, now=NOW) == DAILY_LIMIT - UNIVERSE_BLOCK_AT


def test_missing_budget_file_is_unused(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    assert used(path=path, now=NOW) == 0
    assert remaining(path=path, now=NOW) == DAILY_LIMIT
    assert should_block_universe_scan(path=path, now=NOW) is False


def test_evaluate_and_fetch_flow_do_not_gate_on_universe_budget() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("evaluate.py", "fetch_flow.py"):
        assert "should_block_universe_scan" not in (root / name).read_text()
