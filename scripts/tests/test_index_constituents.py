"""Index constituent resolver: CSV parsing + fallback chain (BPI plan §2).

No network in tests — the HTTP fetch is monkeypatched throughout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from clients import index_constituents as ic

ISHARES_CSV = "\n".join(
    [
        "iShares Core S&P 500 ETF",
        "Fund Holdings as of,\"Jul 23, 2026\"",
        "Inception Date,\"May 15, 2000\"",
        "",
        "Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Quantity,Price,Location,Exchange,Currency,FX Rate,Market Currency,Accrual Date",
        "\"AAPL\",\"APPLE INC\",\"Information Technology\",\"Equity\",\"1.00\",\"7.00\",\"1.00\",\"1\",\"1.00\",\"United States\",\"NASDAQ\",\"USD\",\"1.00\",\"USD\",\"-\"",
        "\"BRK.B\",\"BERKSHIRE HATHAWAY INC CLASS B\",\"Financials\",\"Equity\",\"1.00\",\"1.60\",\"1.00\",\"1\",\"1.00\",\"United States\",\"New York Stock Exchange Inc.\",\"USD\",\"1.00\",\"USD\",\"-\"",
        "\"AAPL\",\"APPLE INC DUPLICATE ROW\",\"Information Technology\",\"Equity\",\"1.00\",\"0.01\",\"1.00\",\"1\",\"1.00\",\"United States\",\"NASDAQ\",\"USD\",\"1.00\",\"USD\",\"-\"",
        "\"XTSLA\",\"BLK CSH FND TREASURY SL AGENCY\",\"Cash and/or Derivatives\",\"Money Market\",\"1.00\",\"0.10\",\"1.00\",\"1\",\"1.00\",\"United States\",\"-\",\"USD\",\"1.00\",\"USD\",\"-\"",
        "\"RTYU6\",\"RUSSELL 2000 EMINI SEP 26\",\"Cash and/or Derivatives\",\"Futures\",\"1.00\",\"0.00\",\"1.00\",\"1\",\"1.00\",\"United States\",\"-\",\"USD\",\"1.00\",\"USD\",\"-\"",
        "\"USD\",\"US DOLLAR\",\"Cash and/or Derivatives\",\"Cash\",\"1.00\",\"0.05\",\"1.00\",\"1\",\"1.00\",\"United States\",\"-\",\"USD\",\"1.00\",\"USD\",\"-\"",
        "",
        "The content contained herein is owned or licensed by BlackRock.",
    ]
)

INVESCO_CSV = "\n".join(
    [
        "Fund Ticker,Security Identifier,Holding Ticker,Shares/Par Value,MarketValue,Weight,Name,Class of Shares,Sector,Date",
        "QQQ,037833100,AAPL   ,100,100.0,7.0,Apple Inc,Common Stock,Information Technology,07/23/2026",
        "QQQ,594918104,MSFT,100,100.0,6.5,Microsoft Corp,Common Stock,Information Technology,07/23/2026",
        "QQQ,594918104,MSFT,100,100.0,6.5,Microsoft Corp DUPE,Common Stock,Information Technology,07/23/2026",
        "QQQ,CASHUSD00,,100,100.0,0.1,US Dollars,Cash,,07/23/2026",
    ]
)


class TestNormalizeTicker:
    def test_dot_class_share_becomes_dash(self):
        assert ic.normalize_ticker("BRK.B") == "BRK-B"

    def test_slash_class_share_becomes_dash(self):
        assert ic.normalize_ticker("BF/B") == "BF-B"

    def test_whitespace_and_case(self):
        assert ic.normalize_ticker(" aapl ") == "AAPL"

    def test_garbage_rejected(self):
        assert ic.normalize_ticker("") is None
        assert ic.normalize_ticker(None) is None
        assert ic.normalize_ticker("--") is None
        assert ic.normalize_ticker("7HYPHENATED-NAME-TOO-LONG") is None


class TestParseIsharesCsv:
    def test_equity_rows_only_normalized_and_deduped(self):
        tickers = ic.parse_ishares_csv(ISHARES_CSV)
        assert tickers == ["AAPL", "BRK-B"]

    def test_no_header_row_returns_empty(self):
        assert ic.parse_ishares_csv("no,holdings,here\n1,2,3") == []


class TestParseInvescoCsv:
    def test_ticker_column_normalized_and_deduped(self):
        assert ic.parse_invesco_csv(INVESCO_CSV) == ["AAPL", "MSFT"]

    def test_missing_ticker_column_returns_empty(self):
        assert ic.parse_invesco_csv("A,B\n1,2") == []


class TestResolveFallbackChain:
    @pytest.fixture
    def dirs(self, tmp_path: Path):
        cache_dir = tmp_path / "cache"
        seed_dir = tmp_path / "seeds"
        seed_dir.mkdir(parents=True)
        (seed_dir / "SPX.json").write_text(
            json.dumps({"fetched_at": "seedstamp", "source": "seed", "tickers": ["SEEDA", "SEEDB"]})
        )
        return cache_dir, seed_dir

    def _patch_min_count(self, monkeypatch):
        monkeypatch.setattr(ic, "MIN_PLAUSIBLE_COUNTS", {"SPX": 2})

    def test_fresh_fetch_wins_and_writes_cache(self, dirs, monkeypatch):
        cache_dir, seed_dir = dirs
        self._patch_min_count(monkeypatch)
        monkeypatch.setattr(ic, "_fetch_csv", lambda url: ISHARES_CSV)
        tickers, source = ic.resolve_constituents("SPX", cache_dir=cache_dir, seed_dir=seed_dir)
        assert (tickers, source) == (["AAPL", "BRK-B"], "ishares")
        cached = json.loads((cache_dir / "SPX.json").read_text())
        assert cached["tickers"] == ["AAPL", "BRK-B"]
        assert cached["source"] == "ishares"
        assert cached["fetched_at"]

    def test_fetch_failure_falls_back_to_cache_with_stale_warning(self, dirs, monkeypatch, capsys):
        cache_dir, seed_dir = dirs
        self._patch_min_count(monkeypatch)
        cache_dir.mkdir(parents=True)
        (cache_dir / "SPX.json").write_text(
            json.dumps({"fetched_at": "oldstamp", "source": "ishares", "tickers": ["CACHED"]})
        )
        monkeypatch.setattr(ic, "_fetch_csv", lambda url: (_ for _ in ()).throw(OSError("boom")))
        tickers, source = ic.resolve_constituents("SPX", cache_dir=cache_dir, seed_dir=seed_dir)
        assert (tickers, source) == (["CACHED"], "cache")
        assert "stale" in capsys.readouterr().err.lower()

    def test_implausibly_small_fetch_falls_through(self, dirs, monkeypatch):
        cache_dir, seed_dir = dirs
        monkeypatch.setattr(ic, "MIN_PLAUSIBLE_COUNTS", {"SPX": 400})
        monkeypatch.setattr(ic, "_fetch_csv", lambda url: ISHARES_CSV)
        tickers, source = ic.resolve_constituents("SPX", cache_dir=cache_dir, seed_dir=seed_dir)
        assert (tickers, source) == (["SEEDA", "SEEDB"], "seed")

    def test_no_fetch_no_cache_uses_seed(self, dirs, monkeypatch):
        cache_dir, seed_dir = dirs
        self._patch_min_count(monkeypatch)
        monkeypatch.setattr(ic, "_fetch_csv", lambda url: (_ for _ in ()).throw(OSError("boom")))
        tickers, source = ic.resolve_constituents("SPX", cache_dir=cache_dir, seed_dir=seed_dir)
        assert (tickers, source) == (["SEEDA", "SEEDB"], "seed")

    def test_everything_missing_raises(self, tmp_path, monkeypatch):
        self._patch_min_count(monkeypatch)
        monkeypatch.setattr(ic, "_fetch_csv", lambda url: (_ for _ in ()).throw(OSError("boom")))
        with pytest.raises(ic.ConstituentResolutionError):
            ic.resolve_constituents(
                "SPX", cache_dir=tmp_path / "nocache", seed_dir=tmp_path / "noseed"
            )

    def test_unknown_index_rejected(self):
        with pytest.raises(ValueError):
            ic.resolve_constituents("DAX")


class TestCommittedSeeds:
    """The repo seeds are the last-resort fallback — pin their shape."""

    SEED_DIR = _SCRIPTS_DIR / "data_seeds" / "constituents"

    @pytest.mark.parametrize(
        "index,minimum", [("NDX", 80), ("SPX", 400), ("RUT", 1200)]
    )
    def test_seed_exists_and_is_plausible(self, index: str, minimum: int):
        seed = json.loads((self.SEED_DIR / f"{index}.json").read_text())
        assert seed["source"] == "seed"
        tickers = seed["tickers"]
        assert len(tickers) >= minimum
        assert all(ic.normalize_ticker(t) == t for t in tickers)
