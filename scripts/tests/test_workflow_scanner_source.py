"""Workflow scanner source uses scanner's DB-backed watchlist seam."""

from __future__ import annotations

from types import SimpleNamespace


def test_run_scanner_uses_scanner_watchlist_items_without_watchlist_constant() -> None:
    import workflow.nodes as nodes

    calls: list[str] = []

    def process(item: dict):
        calls.append(item["ticker"])
        return {"ticker": item["ticker"], "score": item["score"]}

    scanner_mod = SimpleNamespace(
        get_open_positions=lambda: {"MSFT"},
        get_watchlist_items=lambda: [
            {"ticker": "AAPL", "score": 7},
            {"ticker": "MSFT", "score": 99},
            {"ticker": "NVDA", "score": 5},
        ],
        _process_ticker=process,
    )

    rows = nodes._run_scanner(scanner_mod, top_n=1, min_score=0)

    assert calls == ["AAPL", "NVDA"]
    assert rows == [{"ticker": "AAPL", "score": 7}]
