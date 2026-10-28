"""PDF relevance is stricter than the newsfeed and needs no PDF/model request."""
import json

import pytest

from research import marketdesk_pdf_policy as policy


def check(title, content, *, tickers=("AAPL",), rules=None, publisher="UBS"):
    return policy.decide(title=title, content=content, publisher=publisher,
                         context=policy.Context(frozenset(tickers), rules or {}))


@pytest.mark.parametrize("ticker", ["AAPL", "AAOI"])
def test_portfolio_and_watchlist_names_selected(ticker):
    assert check("Company earnings", f"${ticker} raised its revenue outlook 12%.", tickers=(ticker,)) == (True,"PDF_BOOK_OR_WATCHLIST")


def test_off_book_name_does_not_spend_pdf_allowance():
    assert check("Company earnings", "$MSFT revenue rose 12%.")[1] == "SINGLE_STOCK_NOT_IN_BOOK"


@pytest.mark.parametrize("title,body,reason", [
    ("EUR/USD outlook", "Rates moved 12%.", "DOC_TYPE_FX_PAIR_NOTE"),
    ("Week ahead calendar", "Inflation printed 3%.", "PDF_ROUTINE_CALENDAR"),
    ("Election recap", "The coalition has won.", "PDF_NO_MEASURED_RELEVANCE"),
    ("Market commentary", "Rates could rise.", "PDF_NO_MEASURED_RELEVANCE"),
])
def test_irrelevant_or_unmeasured_reports_are_excluded(title, body, reason):
    assert check(title, body) == (False,reason)


def test_measured_macro_positioning_remains_in_scope():
    assert check("US positioning", "Equity flows rose 12% and volatility fell 2%.") == (True,"PDF_MEASURED_MARKET_RESEARCH")


def test_preferred_desk_remains_in_scope_and_approved_denials_win():
    assert check("The Flow Show", "Latest desk analysis", publisher="BofA Global Research")[0]
    assert check("The Flow Show", "Latest desk analysis", publisher="BofA Global Research",
                 rules={"publisher_deny":{"BofA Global Research"}}) == (False,"RULE_PUBLISHER")


def test_market_index_ticker_is_not_an_off_book_company():
    assert check("Options", "$SPX volatility rose 12%.")[0]


def test_rule_or_book_changes_invalidate_cached_selection():
    base = policy.Context(frozenset({"AAPL"}), {})
    assert base.key != policy.Context(frozenset({"MSFT"}), {}).key
    assert base.key != policy.Context(base.tickers, {"series_deny":{"Daily note"}}).key
    assert base.key == policy.Context(frozenset({"AAPL"}), {}).key


def test_context_uses_cloud_watchlist_portfolio_underlyings_and_approved_rules(monkeypatch):
    def execute(sql):
        if "watchlist" in sql:
            return [("AAPL",)]
        if "portfolio_snapshots" in sql:
            return [(json.dumps([{"ticker":"AAOI   261120C00080000"}]),)]
        assert "status='approved'" in sql
        return [("series_deny","Calendar"),("unknown_rule","Ignored")]
    monkeypatch.setattr(policy, "hrana_execute", execute)
    context = policy.load_context()
    assert context.tickers == {"AAPL","AAOI"}
    assert context.rules == {"series_deny":{"Calendar"}}


@pytest.mark.parametrize("rows", [[], [(None,)], [("bad",)], [('{}',)]])
def test_unreadable_portfolio_fails_closed_without_local_cache(monkeypatch, rows):
    monkeypatch.setattr(policy, "hrana_execute", lambda sql: [] if "watchlist" in sql else rows)
    with pytest.raises(policy.SelectionUnavailable, match="book_unavailable"):
        policy.load_context()


@pytest.mark.parametrize("oversized", ["watchlist", "rules"])
def test_truncated_context_never_silently_admits_downloads(monkeypatch, oversized):
    def execute(sql):
        if 'watchlist' in sql:
            return [('AAPL',)] * (5001 if oversized=='watchlist' else 1)
        if 'portfolio_snapshots' in sql:
            return [('[]',)]
        return [('series_deny','old')] * 5001
    monkeypatch.setattr(policy, 'hrana_execute', execute)
    with pytest.raises(policy.SelectionUnavailable):
        policy.load_context()


def test_approved_series_denial_uses_existing_normalized_series_identity():
    assert check('Daily note 2026-10-27', 'Flows rose 12%.', rules={'series_deny':{'daily note'}}) == (False,'SERIES_DENYLIST')


def test_regional_parenthetical_is_not_treated_as_an_off_book_security():
    assert check('US Consumer Trading Update (US)', 'Market positioning rose 12%.')[0]


def test_company_earnings_without_a_grounded_security_does_not_spend_pdf_budget():
    assert not check('Company earnings', 'Company earnings rose 12%.')[0]
    assert check('US earnings', 'Aggregate earnings rose 12%.')[0]
