"""Process-wide UW daily budget (quota day resets 20:00 ET)."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from utils.uw_budget import (
    CALLER_ENV,
    DAILY_LIMIT,
    MAX_TRACKED_KEYS,
    UNIVERSE_BLOCK_AT,
    quota_date,
    record_hit,
    record_hits,
    remaining,
    should_block_universe_scan,
    usage_snapshot,
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
    assert payload["date"] == quota_date(NOW)
    assert payload["count"] == 2
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
    assert payload["date"] == quota_date(AT_RESET)
    assert payload["count"] == 1


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


# ── attribution (who spent the quota) ─────────────────────────────


def test_record_hit_tallies_caller_and_endpoint_class(tmp_path: Path) -> None:
    path = tmp_path / "uw_budget.json"
    record_hit(path=path, now=NOW, caller="garch_convergence", endpoint="stock/AAPL/ohlc/1d")
    record_hit(path=path, now=NOW, caller="garch_convergence", endpoint="stock/MSFT/ohlc/1d")
    record_hit(path=path, now=NOW, caller="vcg_scan", endpoint="/stock/NVDA/iv-rank")

    payload = json.loads(path.read_text())
    assert payload["count"] == 3
    assert payload["by_caller"] == {"garch_convergence": 2, "vcg_scan": 1}
    assert payload["by_endpoint"] == {"stock/<T>/ohlc/1d": 2, "stock/<T>/iv-rank": 1}


def test_caller_label_falls_back_to_env_then_argv(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "uw_budget.json"
    monkeypatch.setenv(CALLER_ENV, "leap-refresh")
    record_hit(path=path, now=NOW, endpoint="stock/AAPL/info")
    assert json.loads(path.read_text())["by_caller"] == {"leap-refresh": 1}

    monkeypatch.delenv(CALLER_ENV)
    monkeypatch.setattr(sys, "argv", ["/home/radon/radon/scripts/vcg_scan.py", "--json"])
    record_hit(path=path, now=NOW, endpoint="stock/AAPL/info")
    assert json.loads(path.read_text())["by_caller"]["vcg_scan"] == 1


def test_usage_snapshot_ranks_spenders(tmp_path: Path) -> None:
    path = tmp_path / "uw_budget.json"
    record_hits(9, path=path, now=NOW, caller="garch_convergence", endpoint="stock/A/ohlc/1d")
    record_hits(4, path=path, now=NOW, caller="web", endpoint="stock/A/info")

    snapshot = usage_snapshot(path=path, now=NOW)
    assert snapshot["used"] == 13
    assert snapshot["top_callers"] == [
        {"name": "garch_convergence", "hits": 9},
        {"name": "web", "hits": 4},
    ]
    assert snapshot["top_endpoints"][0] == {"name": "stock/<T>/ohlc/1d", "hits": 9}


def test_quota_day_rollover_archives_the_spent_day(tmp_path: Path) -> None:
    path = tmp_path / "uw_budget.json"
    record_hits(5, path=path, now=BEFORE_RESET, caller="garch_convergence", endpoint="stock/A/ohlc/1d")
    record_hit(path=path, now=AT_RESET, caller="skew", endpoint="stock/SPX/greeks")

    history = [
        json.loads(line)
        for line in (tmp_path / "uw_budget_history.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert history[-1]["date"] == quota_date(BEFORE_RESET)
    assert history[-1]["count"] == 5
    assert history[-1]["by_caller"] == {"garch_convergence": 5}
    assert json.loads(path.read_text())["by_caller"] == {"skew": 1}


def test_tallies_stay_bounded(tmp_path: Path) -> None:
    path = tmp_path / "uw_budget.json"
    for index in range(MAX_TRACKED_KEYS + 25):
        record_hit(path=path, now=NOW, caller=f"caller-{index}", endpoint=f"probe/{index}")
    payload = json.loads(path.read_text())
    assert payload["count"] == MAX_TRACKED_KEYS + 25
    assert len(payload["by_caller"]) <= MAX_TRACKED_KEYS
    assert len(payload["by_endpoint"]) <= MAX_TRACKED_KEYS


def test_scheduled_scans_do_not_default_to_the_full_index_union() -> None:
    """Unattended GARCH / LEAP scans must not default to `indexes`.

    `indexes` is 2494 tickers at 3 UW requests each (~7.5k hits per scan).
    GARCH alone runs it three times a trading day, so the two schedulers
    spent ~30k of the 40k daily cap before anything else asked for data.
    """
    root = Path(__file__).resolve().parents[2]
    entry_points = (
        "scripts/run_garch_refresh.sh",
        "scripts/run_leap_refresh.sh",
        "web/components/WorkspaceSections.tsx",
    )
    for relative in entry_points:
        source = (root / relative).read_text()
        assert '"indexes"' not in source and "-indexes}" not in source, relative
        assert "largecaps" in source, relative
