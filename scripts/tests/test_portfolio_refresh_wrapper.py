from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_portfolio_refresh_does_not_report_http_failure_as_exit_zero():
    source = (ROOT / "scripts" / "run_portfolio_refresh.sh").read_text()
    assert "CURL_EXIT=$?" in source
    assert '[ "$EXIT_CODE" -ne 0 ] || EXIT_CODE=22' in source
    assert "EXIT_CODE=$?" not in source


def test_refresh_skips_outside_current_exchange_session():
    source = (ROOT / "scripts" / "run_portfolio_refresh.sh").read_text()
    assert "market_state" in source
    assert "get('is_open')" in source
