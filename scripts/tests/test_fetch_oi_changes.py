"""OI change fetch must page the full ticker book before any result cap."""

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

def test_ticker_oi_pages_until_short():
    from fetch_oi_changes import _OI_PAGE_LIMIT, fetch_ticker_oi_changes

    page0 = [
        {"option_symbol": f"A{i}", "oi_diff_plain": 1000 + i, "prev_total_premium": 1_000_000}
        for i in range(_OI_PAGE_LIMIT)
    ]
    page1 = [
        {"option_symbol": "TAIL", "oi_diff_plain": 50_000, "prev_total_premium": 20_000_000},
    ]
    mock_client = MagicMock()
    mock_client.get_stock_oi_change.side_effect = [
        {"data": page0},
        {"data": page1},
    ]
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = False

    with patch("fetch_oi_changes.UWClient", return_value=mock_cm):
        rows = fetch_ticker_oi_changes("MSFT", limit=None)

    assert mock_client.get_stock_oi_change.call_count == 2
    assert mock_client.get_stock_oi_change.call_args_list[0].kwargs["page"] == 0
    assert mock_client.get_stock_oi_change.call_args_list[1].kwargs["page"] == 1
    assert len(rows) == _OI_PAGE_LIMIT + 1
    # Significance sort: largest abs OI first
    assert rows[0]["option_symbol"] == "TAIL"


def test_ticker_oi_limit_applies_after_sort():
    from fetch_oi_changes import fetch_ticker_oi_changes

    data = [
        {"option_symbol": "SMALL", "oi_diff_plain": 10, "prev_total_premium": 100},
        {"option_symbol": "BIG", "oi_diff_plain": 9_999, "prev_total_premium": 5_000_000},
        {"option_symbol": "MID", "oi_diff_plain": 100, "prev_total_premium": 50_000},
    ]
    mock_client = MagicMock()
    mock_client.get_stock_oi_change.return_value = {"data": data}
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = False

    with patch("fetch_oi_changes.UWClient", return_value=mock_cm):
        rows = fetch_ticker_oi_changes("MSFT", limit=2)

    assert [r["option_symbol"] for r in rows] == ["BIG", "MID"]


def test_market_oi_requests_max_limit():
    from fetch_oi_changes import _MARKET_OI_LIMIT, fetch_market_oi_changes

    mock_client = MagicMock()
    mock_client.get_oi_change.return_value = {
        "data": [
            {"option_symbol": "X", "oi_diff_plain": 1, "prev_total_premium": 1},
        ],
    }
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = False

    with patch("fetch_oi_changes.UWClient", return_value=mock_cm):
        rows = fetch_market_oi_changes(limit=1)

    assert mock_client.get_oi_change.call_args.kwargs["limit"] == _MARKET_OI_LIMIT
    assert len(rows) == 1


_DAILY_QUOTA_MSG = "You have hit your daily request limit of 40000 requests."


def _quota_client():
    from clients.uw_client import UWRateLimitError

    mock_client = MagicMock()
    mock_client.get_oi_change.side_effect = UWRateLimitError(_DAILY_QUOTA_MSG)
    mock_client.get_stock_oi_change.side_effect = UWRateLimitError(_DAILY_QUOTA_MSG)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = False
    return mock_client, mock_cm


def _stub_health_and_embargo(monkeypatch, tmp_path):
    import db.writer as writer
    import fetch_oi_changes as m

    healths: list[tuple[str, str, dict]] = []
    mirrors: list = []

    def fake_health(service, state, **kwargs):
        healths.append((service, state, kwargs))

    monkeypatch.setattr(writer, "record_service_health", fake_health, raising=False)
    monkeypatch.setattr(writer, "ensure_no_replica_for_writers", lambda: None, raising=False)
    monkeypatch.setattr(m, "mirror_scan_snapshot", lambda *a, **k: mirrors.append((a, k)))
    if hasattr(m, "_embargo_path"):
        monkeypatch.setattr(m, "_embargo_path", lambda: tmp_path / "oi_changes_uw_embargo.json")
    if hasattr(m, "_now"):
        monkeypatch.setattr(
            m, "_now", lambda: datetime(2026, 8, 14, 20, 0, 40, tzinfo=timezone.utc)
        )
    return healths, mirrors


def _run_main(argv):
    import fetch_oi_changes as m

    monkey_argv = ["fetch_oi_changes.py", *argv]
    with patch.object(sys, "argv", monkey_argv):
        try:
            m.main()
            return 0
        except SystemExit as exc:
            return 0 if exc.code is None else exc.code


def test_market_uw_daily_quota_exits_zero_and_embargoes_until_reset(tmp_path, monkeypatch):
    # 2026-08-14 P1: 20:00 UTC oneshot hit UW's 40k daily cap, sys.exit(1),
    # unit watchdog paged (NRestarts=0). Last snapshot must stay; heartbeat
    # must embargo until 20:00 ET so _check_error does not re-page.
    import fetch_oi_changes as m

    client, cm = _quota_client()
    healths, mirrors = _stub_health_and_embargo(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "UWClient", lambda *a, **k: cm)

    assert _run_main(["--market", "--json"]) == 0
    assert client.get_oi_change.call_count == 1
    assert mirrors == []
    assert healths
    service, state, kwargs = healths[0]
    assert service == "oi-changes"
    assert state == "error"
    assert "daily request limit" in kwargs["error"]["message"]
    assert kwargs["error"]["next_attempt_at"] == "2026-08-15T00:00:00Z"

    client2, cm2 = _quota_client()
    monkeypatch.setattr(m, "UWClient", lambda *a, **k: cm2)
    assert _run_main(["--market", "--json"]) == 0
    assert client2.get_oi_change.call_count == 0
    assert healths[-1][1] == "error"
    assert healths[-1][2]["error"]["next_attempt_at"] == "2026-08-15T00:00:00Z"


def test_market_transient_429_still_fails_oneshot(tmp_path, monkeypatch):
    from clients.uw_client import UWRateLimitError
    import fetch_oi_changes as m

    mock_client = MagicMock()
    mock_client.get_oi_change.side_effect = UWRateLimitError("429 too many requests")
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = False
    _stub_health_and_embargo(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "UWClient", lambda *a, **k: mock_cm)

    assert _run_main(["--market", "--json"]) == 1
    assert mock_client.get_oi_change.call_count == 1


def test_ticker_uw_daily_quota_still_exits_nonzero(tmp_path, monkeypatch):
    import fetch_oi_changes as m

    _client, cm = _quota_client()
    healths, mirrors = _stub_health_and_embargo(monkeypatch, tmp_path)
    monkeypatch.setattr(m, "UWClient", lambda *a, **k: cm)

    assert _run_main(["MSFT", "--json"]) == 1
    assert healths == []
    assert mirrors == []
