"""/orders blotter POST rehydrate must not SendRequest during a 1025 lockout.

The cash-flows panel lives on /orders. useBlotter(true) POSTs /api/blotter
every 5 minutes, which runs journal_rehydrate → FlexQueryFetcher on the
SAME IB_FLEX_TOKEN as cash-flow-sync. That is how staring at the red lozenge
keeps the lockout alive.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.flex_embargo import FlexTokenLocked, record_lockout


@pytest.fixture
def sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "flex_token_embargo.json"
    monkeypatch.setattr("utils.flex_embargo.SIDECAR", path)
    monkeypatch.setattr("utils.flex_embargo._heartbeat", lambda *a, **k: None)
    return path


def test_fetch_executions_skips_sendrequest_when_locked(sidecar: Path):
    from trade_blotter.flex_query import FlexQueryFetcher

    record_lockout("1025", now=datetime(2026, 8, 21, 13, 58, 26, tzinfo=timezone.utc))
    fetcher = FlexQueryFetcher(token="tok", query_id="1422766")
    with patch("trade_blotter.flex_query._http_get_text") as http:
        with pytest.raises(FlexTokenLocked):
            fetcher.fetch_executions(days_back=30)
        http.assert_not_called()


def test_sendrequest_1025_arms_the_shared_lockout(sidecar: Path):
    from trade_blotter.flex_query import FlexQueryFetcher

    body = (
        "<FlexStatementResponse><Status>Fail</Status>"
        "<ErrorCode>1025</ErrorCode>"
        "<ErrorMessage>Too many failed attempts. Please review your "
        "configuration.</ErrorMessage></FlexStatementResponse>"
    )
    fetcher = FlexQueryFetcher(token="tok", query_id="1422766")
    with patch("trade_blotter.flex_query._http_get_text", return_value=body) as http:
        with pytest.raises(RuntimeError, match="1025"):
            fetcher.fetch_executions(days_back=30)
        assert http.call_count == 1
    assert sidecar.exists()


def test_getstatement_1025_aborts_instead_of_polling(sidecar: Path):
    from trade_blotter.flex_query import FlexQueryFetcher

    sent = (
        "<FlexStatementResponse><Status>Success</Status>"
        "<ReferenceCode>REF1</ReferenceCode></FlexStatementResponse>"
    )
    locked = (
        "<FlexStatementResponse><Status>Fail</Status>"
        "<ErrorCode>1025</ErrorCode>"
        "<ErrorMessage>Too many failed attempts.</ErrorMessage>"
        "</FlexStatementResponse>"
    )
    fetcher = FlexQueryFetcher(token="tok", query_id="1422766")
    with patch("trade_blotter.flex_query._http_get_text", side_effect=[sent, locked]) as http, \
         patch("trade_blotter.flex_query.time.sleep"):
        with pytest.raises(RuntimeError, match="1025"):
            fetcher.fetch_executions(days_back=30)
        assert http.call_count == 2
