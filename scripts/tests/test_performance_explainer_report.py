import os

import pytest

from performance_explainer_report import (
    DATA_PATH,
    build_html,
    chart_family_contract,
    chart_role_color,
    load_chart_system,
    load_payload,
)


def _fixture_payload() -> dict:
    """Synthetic performance payload with every key build_html reads.

    Pinned here so build_html has coverage in CI too — the previous version
    of this test read the live gitignored data/performance.json, which made
    it red on any dev box with a stale cache (missing period_label) and a
    silent skip in CI (TEST_AUDIT.md T-003).
    """
    summary = {
        "starting_equity": 250_000.0,
        "ending_equity": 287_500.0,
        "total_return": 0.15,
        "pnl": 37_500.0,
        "trading_days": 148,
        "max_drawdown": -0.082,
        "max_drawdown_duration_days": 21,
        "current_drawdown": -0.01,
        "sharpe_ratio": 1.42,
        "sortino_ratio": 2.05,
        "calmar_ratio": 1.83,
        "information_ratio": 0.66,
        "beta": 0.72,
        "alpha": 0.09,
        "correlation": 0.81,
        "tracking_error": 0.11,
        "annualized_volatility": 0.19,
        "downside_deviation": 0.12,
        "var_95": -0.021,
        "cvar_95": -0.034,
        "ulcer_index": 2.4,
        "tail_ratio": 1.1,
        "skew": -0.3,
        "kurtosis": 4.2,
        "hit_rate": 0.57,
        "best_day": 0.029,
        "worst_day": -0.031,
        "upside_capture": 0.88,
        "downside_capture": 0.61,
    }
    series = [
        {"date": "2026-01-02", "equity": 250_000.0, "benchmark_close": 500.0, "drawdown": 0.0},
        {"date": "2026-04-01", "equity": 262_000.0, "benchmark_close": 512.0, "drawdown": -0.082},
        {"date": "2026-08-06", "equity": 287_500.0, "benchmark_close": 540.0, "drawdown": -0.01},
    ]
    return {
        "summary": summary,
        "benchmark": "SPY",
        "benchmark_total_return": 0.08,
        "period_label": "YTD 2026",
        "as_of": "2026-08-06",
        "last_sync": "2026-08-06T16:05:00-04:00",
        "trades_source": "ib_flex",
        "nav_source": "disk_cache",
        "series": series,
        "warnings": ["1 option leg used a stale close for 2026-03-14"],
        "contracts_missing_history": ["MU 20260717 C 1050"],
        "methodology": {
            "curve_type": "twr_modified_dietz_daily",
            "return_basis": "reconstructed_equity_curve",
            "risk_free_rate": 0.043,
        },
        "price_sources": {"stocks": "IB daily bars", "options": "IB marks + cache"},
    }


def test_chart_family_contract_comes_from_shared_chart_system():
    chart_system = load_chart_system()

    contract = chart_family_contract(chart_system, "analytical-time-series")

    assert contract["id"] == "analytical-time-series"
    assert contract["label"] == "Analytical Time Series"
    assert contract["renderer"] == "svg"
    assert contract["requires_axes"] is True
    assert contract["renderer_description"] == chart_system["sanctionedRenderers"]["svg"]
    assert chart_role_color(chart_system, "comparison") == chart_system["seriesRoles"]["comparison"]["fallback"]


def test_build_html_mentions_shared_chart_contract():
    payload = _fixture_payload()
    chart_system = load_chart_system()

    report_html = build_html(payload, chart_system)

    assert "web/lib/chart-system-spec.json" in report_html
    assert "PRIMARY / COMPARISON" in report_html
    assert "ANALYTICAL TIME SERIES" in report_html
    assert "This panel uses the shared chart-system contract" in report_html


def test_build_html_renders_fixture_figures_and_period_label():
    payload = _fixture_payload()

    report_html = build_html(payload, load_chart_system())

    # period_label drives the hero kicker — its absence was the exact
    # stale-cache KeyError this test previously reproduced on dev boxes.
    assert "RECONSTRUCTED YTD 2026" in report_html
    assert "SPY" in report_html
    assert "148 DAYS" in report_html
    assert "IB FLEX" in report_html


def test_build_html_against_live_cache_smoke():
    """Opt-in smoke against the real runtime cache (never runs in CI)."""
    if os.environ.get("RADON_LIVE_CACHE_SMOKE") != "1":
        pytest.skip("set RADON_LIVE_CACHE_SMOKE=1 to exercise the live data/performance.json")
    if not DATA_PATH.exists():
        pytest.skip("requires data/performance.json runtime cache (run a performance scan first)")
    report_html = build_html(load_payload(), load_chart_system())
    assert "This panel uses the shared chart-system contract" in report_html
