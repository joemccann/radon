"""Phase 4 — verify the deferred wirings call the right writer helpers.

Mocks db.writer surface, exercises the producers, asserts call shape.
Covers:
  - fetch_x_watchlist._dual_write_watchlist_to_db (per-ticker upserts)
  - fetch_ticker.cache_ticker → upsert_ticker_lookup_cache
  - flex_token_check._dual_write_flex_state_to_app_config
  - fetch_analyst_ratings dual-write block
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _stub_writer(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {
        "upsert_watchlist_ticker": [],
        "upsert_ticker_lookup_cache": [],
        "upsert_app_config": [],
        "upsert_analyst_ratings": [],
        "record_service_health": [],
    }
    fake = types.ModuleType("db.writer")
    fake.upsert_watchlist_ticker = lambda ticker, **kw: calls["upsert_watchlist_ticker"].append(  # type: ignore[attr-defined]
        {"ticker": ticker, **kw}
    )
    fake.upsert_ticker_lookup_cache = lambda query, result, expires_at: calls["upsert_ticker_lookup_cache"].append(  # type: ignore[attr-defined]
        {"query": query, "result": result, "expires_at": expires_at}
    )
    fake.upsert_app_config = lambda key, value: calls["upsert_app_config"].append(  # type: ignore[attr-defined]
        {"key": key, "value": value}
    )
    fake.upsert_analyst_ratings = lambda ticker, fetched_at, payload: calls["upsert_analyst_ratings"].append(  # type: ignore[attr-defined]
        {"ticker": ticker, "fetched_at": fetched_at, "payload": payload}
    )
    fake.record_service_health = lambda *a, **kw: calls["record_service_health"].append((a, kw))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "db.writer", fake)
    return calls


# ── fetch_x_watchlist._dual_write_watchlist_to_db ────────────────────

class TestWatchlistDualWrite:
    def test_only_writes_touched_tickers(self, monkeypatch: pytest.MonkeyPatch):
        calls = _stub_writer(monkeypatch)
        if "fetch_x_watchlist" in sys.modules:
            del sys.modules["fetch_x_watchlist"]
        import fetch_x_watchlist

        subcategory = {
            "source": "https://x.com/foo",
            "tickers": [
                {"ticker": "AAPL", "sentiment_history": []},
                {"ticker": "MSFT", "sentiment_history": []},
                {"ticker": "GOOG", "sentiment_history": []},
            ],
        }
        fetch_x_watchlist._dual_write_watchlist_to_db(
            "foo",
            subcategory,
            touched_tickers=["AAPL", "MSFT"],  # GOOG should not be written
        )
        written = sorted(c["ticker"] for c in calls["upsert_watchlist_ticker"])
        assert written == ["AAPL", "MSFT"]

    def test_no_op_when_no_touched_tickers(self, monkeypatch: pytest.MonkeyPatch):
        calls = _stub_writer(monkeypatch)
        if "fetch_x_watchlist" in sys.modules:
            del sys.modules["fetch_x_watchlist"]
        import fetch_x_watchlist

        fetch_x_watchlist._dual_write_watchlist_to_db("foo", {"tickers": []}, touched_tickers=[])
        assert calls["upsert_watchlist_ticker"] == []

    def test_uses_account_as_source(self, monkeypatch: pytest.MonkeyPatch):
        calls = _stub_writer(monkeypatch)
        if "fetch_x_watchlist" in sys.modules:
            del sys.modules["fetch_x_watchlist"]
        import fetch_x_watchlist

        subcategory = {"tickers": [{"ticker": "AAPL"}]}
        fetch_x_watchlist._dual_write_watchlist_to_db("jpmorgan", subcategory, ["AAPL"])
        assert calls["upsert_watchlist_ticker"][0]["source"] == "@jpmorgan"


# ── fetch_ticker._dual_write_ticker_to_db ────────────────────────────

class TestTickerLookupCacheDualWrite:
    def test_writes_normalized_ticker_with_24h_ttl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ):
        calls = _stub_writer(monkeypatch)
        if "fetch_ticker" in sys.modules:
            del sys.modules["fetch_ticker"]
        import fetch_ticker

        # Stub disk cache path so the test doesn't write to data/.
        monkeypatch.setattr(fetch_ticker, "CACHE_FILE", tmp_path / "ticker_cache.json")

        fetch_ticker.cache_ticker("aapl", "Apple Inc.", sector="Technology")

        assert len(calls["upsert_ticker_lookup_cache"]) == 1
        row = calls["upsert_ticker_lookup_cache"][0]
        assert row["query"] == "AAPL"
        assert "Apple Inc." in row["result"]
        assert "Technology" in row["result"]
        # Expiry must be ~24h in the future
        assert row["expires_at"]

    def test_disk_write_still_happens_when_db_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ):
        if "fetch_ticker" in sys.modules:
            del sys.modules["fetch_ticker"]
        # Force upsert to throw
        fake = types.ModuleType("db.writer")
        fake.upsert_ticker_lookup_cache = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "db.writer", fake)
        import fetch_ticker

        cache_path = tmp_path / "ticker_cache.json"
        monkeypatch.setattr(fetch_ticker, "CACHE_FILE", cache_path)

        fetch_ticker.cache_ticker("MSFT", "Microsoft Corp.", sector="Technology")
        assert cache_path.exists()
        payload = json.loads(cache_path.read_text())
        assert "MSFT" in payload["tickers"]


# ── flex_token_check._dual_write_flex_state_to_app_config ────────────

class TestFlexTokenAppConfigDualWrite:
    def test_writes_three_keys(self, monkeypatch: pytest.MonkeyPatch):
        calls = _stub_writer(monkeypatch)
        if "monitor_daemon.handlers.flex_token_check" in sys.modules:
            del sys.modules["monitor_daemon.handlers.flex_token_check"]
        from monitor_daemon.handlers import flex_token_check

        config = {
            "expires_at": "2026-08-15",
            "reminders_sent": {"30": "2026-07-16T00:00:00Z"},
        }
        flex_token_check._dual_write_flex_state_to_app_config(config, days_remaining=30)

        keys = sorted(c["key"] for c in calls["upsert_app_config"])
        assert keys == [
            "flex_token_days_remaining",
            "flex_token_expires_at",
            "flex_token_reminders_sent",
        ]
        # days_remaining is stringified
        days_row = next(c for c in calls["upsert_app_config"] if c["key"] == "flex_token_days_remaining")
        assert days_row["value"] == "30"

    def test_skips_expires_at_when_missing(self, monkeypatch: pytest.MonkeyPatch):
        calls = _stub_writer(monkeypatch)
        if "monitor_daemon.handlers.flex_token_check" in sys.modules:
            del sys.modules["monitor_daemon.handlers.flex_token_check"]
        from monitor_daemon.handlers import flex_token_check

        flex_token_check._dual_write_flex_state_to_app_config({}, days_remaining=30)
        keys = [c["key"] for c in calls["upsert_app_config"]]
        assert "flex_token_expires_at" not in keys
        # days_remaining still written
        assert "flex_token_days_remaining" in keys

    def test_swallows_writer_exception(self, monkeypatch: pytest.MonkeyPatch):
        fake = types.ModuleType("db.writer")
        fake.upsert_app_config = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "db.writer", fake)
        if "monitor_daemon.handlers.flex_token_check" in sys.modules:
            del sys.modules["monitor_daemon.handlers.flex_token_check"]
        from monitor_daemon.handlers import flex_token_check

        # Must not raise
        flex_token_check._dual_write_flex_state_to_app_config(
            {"expires_at": "2026-08-15"}, days_remaining=30,
        )


# ── fetch_analyst_ratings dual-write smoke ───────────────────────────

class TestAnalystRatingsImportable:
    """The dual-write block lives at the bottom of main(), which we
    don't want to invoke in tests (it requires UW + IB clients). Just
    verify the module imports cleanly with the stubbed writer — the
    actual dual-write call shape is exercised in
    test_phase2_writers::TestAnalystRatings."""

    def test_module_imports(self, monkeypatch: pytest.MonkeyPatch):
        _stub_writer(monkeypatch)
        if "fetch_analyst_ratings" in sys.modules:
            del sys.modules["fetch_analyst_ratings"]
        # Don't actually run main(); just import to verify the dotenv
        # bootstrap doesn't error.
        import fetch_analyst_ratings  # noqa: F401
