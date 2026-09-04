from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

WRAPPER = ROOT / "scripts" / "run_portfolio_refresh.sh"
TIMER = ROOT / "cloud" / "services" / "radon-portfolio-sync.timer"


def test_portfolio_refresh_does_not_report_http_failure_as_exit_zero():
    source = WRAPPER.read_text()
    assert "CURL_EXIT=$?" in source
    assert '[ "$EXIT_CODE" -ne 0 ] || EXIT_CODE=22' in source
    assert "EXIT_CODE=$?" not in source


def test_refresh_gate_covers_the_extended_equity_session():
    """Positions must track fills whenever fills can happen.

    ``fill_monitor`` runs session_window=equity_ext (04:00-20:00 ET), so an
    outsideRth fill mirrors into ``executed_orders`` and raises a FILLED
    toast after the cash close. Gating this wrapper on the RTH-only
    ``market_state()['is_open']`` left the positions table pre-fill until
    the next RTH open. Same defect class as the AVGO 2026-09-02 EXT fill.
    """
    source = WRAPPER.read_text()
    assert "is_equity_ext_session_et" in source
    assert "get('is_open')" not in source


def test_portfolio_sync_timer_spans_the_extended_session():
    """The unit gate is only as wide as the timer that fires it."""
    source = TIMER.read_text()
    assert "OnCalendar=Mon..Fri *-*-* 04..19:*:00 America/New_York" in source
    assert "09..16" not in source
