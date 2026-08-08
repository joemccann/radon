"""OI change fetch must page the full ticker book before any result cap."""

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
