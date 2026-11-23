"""PERF-060: `data/equibles_short_crowding.json` must persist compact JSON.

Pretty-printed `indent=2` on the dual-write inflated the fallback file.
A production-shaped board (MAX_TICKERS=40 names, SETTLEMENT_HISTORY_LIMIT=26
bi-monthly rows each, BOARD_ROW_LIMIT=25 snapshot rows) serializes to
279_858 pretty bytes versus 186_697 compact, rewritten every
`radon-equibles-short-crowding` cycle.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import fetch_equibles_short_crowding as mod


def _production_payload() -> dict:
    today = date(2026, 11, 17)
    latest = today - timedelta(days=9)
    entries = []
    for i in range(mod.MAX_TICKERS):
        ticker = f"T{i:02d}"
        rows = []
        settled = latest
        for _ in range(mod.SETTLEMENT_HISTORY_LIMIT):
            rows.append(
                {
                    "settlementDate": settled.isoformat(),
                    "shortPosition": 10_000_000 + i * 1_000,
                    "changeInShortPosition": 100_000,
                    "averageDailyVolume": 5_000_000,
                    "daysToCover": 2.5 + (i % 4),
                }
            )
            settled -= timedelta(days=14)
        squeeze = {
            "data": [
                {
                    "ticker": ticker,
                    "score": 40.0 + i,
                    "rank": i + 1,
                    "baseScore": 30.0,
                    "catalystBoost": 10.0,
                    "marketCap": 1_000_000_000,
                    "dollarVolume": 50_000_000,
                    "shortInterestPctOfFloat": 4.23,
                    "daysToCoverPercentile": 88.0,
                }
            ]
        }
        entries.append(
            mod.build_ticker_entry(
                ticker,
                mod.normalize_short_interest_rows({"data": rows}),
                mod.normalize_squeeze_score(squeeze),
                today=today,
            )
        )
    board = mod.normalize_board_rows(
        {
            "settlementDate": latest.isoformat(),
            "data": [
                {
                    "ticker": f"B{i:02d}",
                    "shortPosition": 1_000_000,
                    "daysToCover": 2.0,
                    "averageDailyVolume": 3_000_000,
                }
                for i in range(mod.BOARD_ROW_LIMIT)
            ],
        }
    )
    return mod.build_payload(
        entries=entries,
        board=board,
        scan_time="2026-11-17T21:00:00Z",
        requests_used=80,
    )


def test_short_crowding_json_cache_is_compact(tmp_path, monkeypatch):
    payload = _production_payload()
    path = tmp_path / "equibles_short_crowding.json"
    monkeypatch.setattr(mod, "SHORT_CROWDING_JSON", path)
    mod._write_json_cache(payload)

    raw = path.read_bytes()
    compact = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    pretty = json.dumps(payload, indent=2).encode("utf-8")
    assert json.loads(raw) == payload
    assert len(pretty) > len(compact)
    assert len(raw) == len(compact), (
        f"cache bytes {len(raw)} compact {len(compact)} pretty {len(pretty)}"
    )
    assert raw == compact
