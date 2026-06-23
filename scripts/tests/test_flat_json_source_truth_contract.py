"""Static contracts for Turso-only runtime source-of-truth paths."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _code_without_comments(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _assert_no_legacy_data_files(path: Path) -> None:
    text = _code_without_comments(path)
    forbidden = [
        "data/portfolio.json",
        "data/trade_log.json",
        "data/orders.json",
        "data/blotter.json",
        "portfolio.json",
        "trade_log.json",
        "orders.json",
        "blotter.json",
    ]
    for token in forbidden:
        assert token not in text, f"{path} must not reference {token}"

    disk_patterns = [
        r"\bopen\(",
        r"\.read_text\(",
        r"\.write_text\(",
        r"atomic_save\(",
    ]
    for pattern in disk_patterns:
        if re.search(pattern, text):
            allowed_nav_history = "nav_history.jsonl" in text and path.name == "ib_sync.py"
            allowed_price_cache = "price_history_cache" in text and path.name == "portfolio_risk.py"
            allowed_flow_cache = "flow_surprise.json" in text and path.name == "flow_surprise.py"
            allowed_report_output = path.name in {
                "scenario_analysis.py",
                "leap_iv_scanner.py",
                "portfolio_report.py",
            }
            allowed_ratings_cache = "analyst_ratings_cache.json" in text and path.name == "fetch_analyst_ratings.py"
            allowed_service_log = "exit_order_service.log" in text and path.name == "exit_order_service.py"
            allowed_strategy_config = "strategies.json" in text and path.name == "portfolio_attribution.py"
            allowed_performance_cache = path.name == "portfolio_performance.py" and (
                "IB_NAV_CACHE_PATH" in text
                or "ib_twr_series.json" in text
                or "NAV_HISTORY_PATH" in text
            )
            assert (
                allowed_nav_history
                or allowed_price_cache
                or allowed_flow_cache
                or allowed_report_output
                or allowed_ratings_cache
                or allowed_service_log
                or allowed_strategy_config
                or allowed_performance_cache
            ), (
                f"{path} must not read or write flat JSON source files"
            )


def test_trade_portfolio_scripts_do_not_use_flat_json_source_truth() -> None:
    for rel in [
        "scripts/ib_sync.py",
        "scripts/ib_orders.py",
        "scripts/ib_execute.py",
        "scripts/journal_rehydrate.py",
        "scripts/scanner.py",
        "scripts/flow_analysis.py",
        "scripts/discover.py",
        "scripts/portfolio_risk.py",
        "scripts/backfill_journal_from_executed_orders.py",
        "scripts/workflow/nodes.py",
        "scripts/sweep_informed_flow.py",
        "scripts/forecasting/flow_surprise.py",
        "scripts/forecasting/calibration_report.py",
        "scripts/naked_short_audit.py",
        "scripts/scenario_analysis.py",
        "scripts/leap_iv_scanner.py",
        "scripts/fetch_analyst_ratings.py",
        "scripts/free_trade_analyzer.py",
        "scripts/exit_order_service.py",
        "scripts/portfolio_attribution.py",
        "scripts/fetch_x_watchlist.py",
        "scripts/fetch_x_xai.py",
        "scripts/portfolio_report.py",
        "scripts/portfolio_performance.py",
        "scripts/ib_reconcile.py",
        "scripts/db/bootstrap_journal.py",
        "scripts/utils/incremental_sync.py",
    ]:
        _assert_no_legacy_data_files(PROJECT_ROOT / rel)
