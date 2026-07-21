"""RV-ratio history layer — ensure_history (rv-ratio plan step 4, section 5.2).

Fully mocked fetchers (no network, no IB, no UW, no Yahoo) against the
in-memory sqlite schema from migration 0029 (the test_rv_ratio_writer.py
fixture pattern). All dates are window-relative (today-minus-N).
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import rv_ratio_scan as rv

_MIGRATION = _SCRIPTS_DIR / "db" / "migrations" / "0029_rv_ratio.sql"


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db_with_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """In-memory sqlite with schema_migrations bootstrap + 0029 applied."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          applied_at TEXT NOT NULL
        )
        """
    )
    for stmt in _split_statements(_MIGRATION.read_text(encoding="utf-8")):
        conn.execute(stmt)
    conn.commit()

    import db.client as client_mod
    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)

    import importlib
    import db.writer as writer_mod
    importlib.reload(writer_mod)

    try:
        yield conn
    finally:
        conn.close()


def _session(days_back: int) -> str:
    """Window-relative session date (today-minus-N), never hardcoded."""
    return (date.today() - timedelta(days=days_back)).isoformat()


CURRENT_SESSION = _session(0)


@pytest.fixture(autouse=True)
def pinned_session(monkeypatch: pytest.MonkeyPatch):
    """Pin the last completed ET session to today (calendar-day granularity)."""
    monkeypatch.setattr(rv, "last_completed_session_date", lambda: CURRENT_SESSION)


@pytest.fixture(autouse=True)
def fetcher_tripwires(monkeypatch: pytest.MonkeyPatch):
    """Every fetcher trips unless a test explicitly overrides it."""
    def _trip(name):
        def _boom(*args, **kwargs):
            raise AssertionError(f"{name} must not be called in this test")
        return _boom

    monkeypatch.setattr(rv, "_fetch_ib_daily", _trip("_fetch_ib_daily"))
    monkeypatch.setattr(rv, "_fetch_uw_daily", _trip("_fetch_uw_daily"))
    monkeypatch.setattr(rv, "_fetch_yahoo_daily", _trip("_fetch_yahoo_daily"))
    monkeypatch.setattr(rv, "_ib_auth_state", lambda: "authenticated")


def _closes(days_back_newest: int, n: int, base: float = 100.0) -> dict[str, float]:
    """n ascending daily closes ending days_back_newest days ago."""
    return {
        _session(days_back_newest + n - 1 - i): base + i
        for i in range(n)
    }


def _seed(
    conn: sqlite3.Connection,
    symbol: str,
    closes: dict[str, float],
    source: str = "yahoo",
    fetched_at: str = "seed",
) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO price_history_daily (symbol, date, close, source, fetched_at)"
        " VALUES (?, ?, ?, ?, ?)",
        [(symbol, d, c, source, fetched_at) for d, c in closes.items()],
    )
    conn.commit()


def _db_rows(conn: sqlite3.Connection, symbol: str) -> dict[str, tuple[float, str]]:
    rows = conn.execute(
        "SELECT date, close, source FROM price_history_daily WHERE symbol = ? ORDER BY date",
        (symbol,),
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


# ── cold backfill ────────────────────────────────────────────────────

class TestColdBackfill:
    def test_empty_store_backfills_from_yahoo_and_writes_rows(self, db_with_schema, monkeypatch):
        fetched = _closes(0, 300)
        calls = []

        def _yahoo(symbol, years):
            calls.append((symbol, years))
            return dict(fetched)

        monkeypatch.setattr(rv, "_fetch_yahoo_daily", _yahoo)

        closes, sources = rv.ensure_history("SMH")

        assert calls == [("SMH", rv.BACKFILL_CALENDAR_YEARS)]
        assert closes == fetched
        assert sources == ["yahoo"]
        rows = _db_rows(db_with_schema, "SMH")
        assert len(rows) == 300
        assert all(src == "yahoo" for _, src in rows.values())

    def test_returned_series_trimmed_to_trailing_closes_target(self, db_with_schema, monkeypatch):
        extra = 10
        fetched = _closes(0, rv.CLOSES_TARGET + extra)
        monkeypatch.setattr(rv, "_fetch_yahoo_daily", lambda symbol, years: dict(fetched))

        closes, _ = rv.ensure_history("SMH")

        assert len(closes) == rv.CLOSES_TARGET
        assert min(closes) == sorted(fetched)[extra]
        assert max(closes) == max(fetched)
        # The store keeps everything that was fetched.
        assert len(_db_rows(db_with_schema, "SMH")) == rv.CLOSES_TARGET + extra

    def test_backfill_failure_with_empty_store_writes_nothing(self, db_with_schema, monkeypatch):
        monkeypatch.setattr(rv, "_fetch_yahoo_daily", lambda symbol, years: {})

        closes, sources = rv.ensure_history("SMH")

        assert closes == {}
        assert sources == []
        assert _db_rows(db_with_schema, "SMH") == {}

    def test_deep_store_skips_backfill(self, db_with_schema):
        """Full-depth store that is current: zero fetcher calls (SPY skip path)."""
        stored = _closes(0, rv.CLOSES_TARGET, base=500.0)
        _seed(db_with_schema, "SPY", stored)

        closes, sources = rv.ensure_history("SPY")

        assert closes == stored
        assert sources == []

    def test_old_shallow_store_skips_backfill(self, db_with_schema):
        """Earliest row older than ~11y: as deep as history gets, no re-fetch."""
        stored = _closes(0, 30)
        ancient = _session(365 * 12)
        stored[ancient] = 50.0
        _seed(db_with_schema, "SMH", stored)

        closes, sources = rv.ensure_history("SMH")

        assert closes == stored
        assert sources == []


# ── incremental refresh ──────────────────────────────────────────────

def _stale_store(conn, symbol: str = "SMH", newest_days_back: int = 5) -> dict[str, float]:
    """Shallow store ending newest_days_back sessions ago, with an ancient
    anchor so the deep-backfill gate stays closed."""
    stored = _closes(newest_days_back, 30)
    stored[_session(365 * 12)] = 50.0
    _seed(conn, symbol, stored)
    return stored


class TestIncrementalRefresh:
    def test_gap_fill_prefers_ib_and_upserts_only_new_dates(self, db_with_schema, monkeypatch):
        stored = _stale_store(db_with_schema)
        overlap = {d: stored[d] for d in sorted(stored)[-5:]}
        new_dates = _closes(0, 5, base=200.0)
        monkeypatch.setattr(rv, "_fetch_ib_daily", lambda symbol: {**overlap, **new_dates})

        closes, sources = rv.ensure_history("SMH")

        assert sources == ["ib"]
        assert closes == {**stored, **new_dates}
        rows = _db_rows(db_with_schema, "SMH")
        for d in new_dates:
            assert rows[d] == (new_dates[d], "ib")
        for d in overlap:
            assert rows[d][1] == "yahoo"  # stored rows untouched

    def test_ib_preflight_skip_falls_to_uw(self, db_with_schema, monkeypatch):
        _stale_store(db_with_schema)
        monkeypatch.setattr(rv, "_ib_auth_state", lambda: "awaiting_2fa")
        new_dates = _closes(0, 5, base=200.0)
        monkeypatch.setattr(rv, "_fetch_uw_daily", lambda symbol: dict(new_dates))

        closes, sources = rv.ensure_history("SMH")

        assert sources == ["uw"]
        for d in new_dates:
            assert closes[d] == new_dates[d]

    def test_ib_empty_falls_to_uw(self, db_with_schema, monkeypatch):
        _stale_store(db_with_schema)
        monkeypatch.setattr(rv, "_fetch_ib_daily", lambda symbol: {})
        new_dates = _closes(0, 5, base=200.0)
        monkeypatch.setattr(rv, "_fetch_uw_daily", lambda symbol: dict(new_dates))

        _, sources = rv.ensure_history("SMH")

        assert sources == ["uw"]

    def test_index_symbol_skips_uw_falls_to_yahoo(self, db_with_schema, monkeypatch, capsys):
        _stale_store(db_with_schema, symbol="SPX")
        monkeypatch.setattr(rv, "_fetch_ib_daily", lambda symbol: {})
        calls = []

        def _yahoo(symbol, years):
            calls.append((symbol, years))
            return _closes(0, 5, base=200.0)

        monkeypatch.setattr(rv, "_fetch_yahoo_daily", _yahoo)

        _, sources = rv.ensure_history("SPX")

        assert sources == ["yahoo"]
        assert calls == [("SPX", rv.INCREMENTAL_YAHOO_YEARS)]
        assert "UW skipped" in capsys.readouterr().err

    def test_current_store_skips_incremental(self, db_with_schema):
        stored = _stale_store(db_with_schema, newest_days_back=0)

        closes, sources = rv.ensure_history("SMH")

        assert closes == stored
        assert sources == []

    def test_all_sources_fail_serves_stored(self, db_with_schema, monkeypatch):
        stored = _stale_store(db_with_schema)
        monkeypatch.setattr(rv, "_fetch_ib_daily", lambda symbol: {})
        monkeypatch.setattr(rv, "_fetch_uw_daily", lambda symbol: {})
        monkeypatch.setattr(rv, "_fetch_yahoo_daily", lambda symbol, years: {})

        closes, sources = rv.ensure_history("SMH")

        assert closes == stored
        assert sources == []
        assert len(_db_rows(db_with_schema, "SMH")) == len(stored)


# ── splice validation (corporate-action detector) ────────────────────

class TestSpliceValidation:
    def test_mismatch_triggers_delete_and_full_yahoo_rebackfill(self, db_with_schema, monkeypatch):
        stored = _stale_store(db_with_schema)
        # A split halves every overlapping close: > 3 dates beyond 0.5%.
        halved_overlap = {d: stored[d] / 2 for d in sorted(stored)[-6:]}
        new_dates = _closes(0, 5, base=60.0)
        monkeypatch.setattr(rv, "_fetch_ib_daily", lambda symbol: {**halved_overlap, **new_dates})
        fresh = _closes(0, 40, base=55.0)
        yahoo_calls = []

        def _yahoo(symbol, years):
            yahoo_calls.append((symbol, years))
            return dict(fresh)

        monkeypatch.setattr(rv, "_fetch_yahoo_daily", _yahoo)

        closes, sources = rv.ensure_history("SMH")

        assert yahoo_calls == [("SMH", rv.BACKFILL_CALENDAR_YEARS)]
        assert sources == ["yahoo"]
        assert closes == fresh
        rows = _db_rows(db_with_schema, "SMH")
        assert set(rows) == set(fresh)  # old lineage deleted
        assert all(src == "yahoo" for _, src in rows.values())

    def test_exactly_three_mismatches_is_tolerated(self, db_with_schema, monkeypatch):
        stored = _stale_store(db_with_schema)
        overlap_dates = sorted(stored)[-5:]
        overlap = {d: stored[d] for d in overlap_dates}
        for d in overlap_dates[:3]:
            overlap[d] = stored[d] * 1.02  # 2% off on exactly 3 dates
        new_dates = _closes(0, 5, base=200.0)
        monkeypatch.setattr(rv, "_fetch_ib_daily", lambda symbol: {**overlap, **new_dates})

        closes, sources = rv.ensure_history("SMH")

        assert sources == ["ib"]
        assert closes == {**stored, **new_dates}  # stored values win on overlap

    def test_rebackfill_fetch_failure_keeps_stored_lineage(self, db_with_schema, monkeypatch):
        stored = _stale_store(db_with_schema)
        halved_overlap = {d: stored[d] / 2 for d in sorted(stored)[-6:]}
        monkeypatch.setattr(rv, "_fetch_ib_daily", lambda symbol: halved_overlap)
        monkeypatch.setattr(rv, "_fetch_yahoo_daily", lambda symbol, years: {})

        closes, sources = rv.ensure_history("SMH")

        assert closes == stored
        assert sources == []
        assert len(_db_rows(db_with_schema, "SMH")) == len(stored)


# ── completed-session clamp (in-progress candles never stored) ───────

class TestCompletedSessionClamp:
    def test_intraday_partial_candle_is_never_stored(self, db_with_schema, monkeypatch):
        """Intraday fetches include today's in-progress bar; only bars up to
        the last COMPLETED session may enter the durable close store (stored
        rows win later, so one partial close would poison 252 sessions)."""
        completed = _session(1)
        monkeypatch.setattr(rv, "last_completed_session_date", lambda: completed)
        _stale_store(db_with_schema, newest_days_back=5)
        fetched = _closes(0, 5, base=200.0)  # includes today's partial bar
        monkeypatch.setattr(rv, "_fetch_ib_daily", lambda symbol: dict(fetched))

        closes, sources = rv.ensure_history("SMH")

        assert sources == ["ib"]
        assert _session(0) not in closes
        rows = _db_rows(db_with_schema, "SMH")
        assert _session(0) not in rows
        for d in fetched:
            if d <= completed:
                assert rows[d] == (fetched[d], "ib")

    def test_store_current_through_completed_session_skips_refresh(self, db_with_schema, monkeypatch):
        """Intraday, yesterday's close is the correct latest point — a store
        current through the last completed session needs no fetch at all."""
        completed = _session(1)
        monkeypatch.setattr(rv, "last_completed_session_date", lambda: completed)
        stored = _stale_store(db_with_schema, newest_days_back=1)

        closes, sources = rv.ensure_history("SMH")

        assert closes == stored
        assert sources == []


# ── young-listing deep-backfill gate + deep splice validation ────────

def _iso_utc(hours_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _young_store(
    conn, symbol: str = "RDDT", newest_days_back: int = 5, fetched_at: str = "seed"
) -> dict[str, float]:
    """Shallow store WITHOUT an ancient anchor: depth < CLOSES_TARGET and an
    earliest row younger than the 11y wall — a young listing."""
    stored = _closes(newest_days_back, 40)
    _seed(conn, symbol, stored, fetched_at=fetched_at)
    return stored


class TestYoungListingDeepBackfillGate:
    def test_recent_deepen_attempt_skips_backfill_and_keeps_priority_chain(
        self, db_with_schema, monkeypatch
    ):
        stored = _young_store(db_with_schema, fetched_at=_iso_utc())
        new_dates = _closes(0, 5, base=200.0)
        monkeypatch.setattr(rv, "_fetch_ib_daily", lambda symbol: dict(new_dates))

        closes, sources = rv.ensure_history("RDDT")

        assert sources == ["ib"]  # recent segment via IB; no 12y Yahoo refetch
        assert closes == {**stored, **new_dates}

    def test_stale_deepen_attempt_rechecks_yahoo_once(self, db_with_schema, monkeypatch):
        stored = _young_store(
            db_with_schema, newest_days_back=0, fetched_at=_iso_utc(hours_ago=48)
        )
        yahoo_calls = []

        def _yahoo(symbol, years):
            yahoo_calls.append((symbol, years))
            return dict(stored)  # nothing older exists: as deep as Yahoo goes

        monkeypatch.setattr(rv, "_fetch_yahoo_daily", _yahoo)

        first, _ = rv.ensure_history("RDDT")

        assert yahoo_calls == [("RDDT", rv.BACKFILL_CALENDAR_YEARS)]

        # The attempt is recorded on the earliest row, so the very next run
        # must NOT refetch 12 calendar years again.
        def _boom(symbol, years):
            raise AssertionError("deep backfill must not refetch immediately")

        monkeypatch.setattr(rv, "_fetch_yahoo_daily", _boom)
        second, sources = rv.ensure_history("RDDT")

        assert second == first
        assert sources == []

    def test_deep_backfill_splice_mismatch_resets_lineage(self, db_with_schema, monkeypatch):
        """A split between scans halves the whole fetched history; the deep
        path must reset the lineage instead of keeping pre-split stored rows
        (mixed lineage fabricates a ~-50% log-return spike)."""
        stored = _young_store(
            db_with_schema, newest_days_back=0, fetched_at=_iso_utc(hours_ago=48)
        )
        fresh = {d: c / 2 for d, c in stored.items()}
        monkeypatch.setattr(rv, "_fetch_yahoo_daily", lambda symbol, years: dict(fresh))

        closes, sources = rv.ensure_history("RDDT")

        assert sources == ["yahoo"]
        assert closes == fresh
        rows = _db_rows(db_with_schema, "RDDT")
        assert set(rows) == set(fresh)
        assert all(rows[d][0] == fresh[d] for d in fresh)


# ── writer: touch_price_history_row ──────────────────────────────────

class TestTouchPriceHistoryRow:
    def test_refreshes_fetched_at_of_only_that_row(self, db_with_schema):
        closes = _closes(0, 2)
        _seed(db_with_schema, "SMH", closes)

        import db.writer as writer_mod
        writer_mod.touch_price_history_row("SMH", min(closes))

        rows = db_with_schema.execute(
            "SELECT date, fetched_at FROM price_history_daily"
            " WHERE symbol = 'SMH' ORDER BY date"
        ).fetchall()
        assert rows[0][1] != "seed"
        assert rows[1][1] == "seed"


# ── writer: delete_price_history ─────────────────────────────────────

class TestDeletePriceHistory:
    def test_removes_only_that_symbol(self, db_with_schema):
        _seed(db_with_schema, "SMH", _closes(0, 3))
        _seed(db_with_schema, "SPY", _closes(0, 3, base=500.0))

        import db.writer as writer_mod
        writer_mod.delete_price_history("SMH")

        assert _db_rows(db_with_schema, "SMH") == {}
        assert len(_db_rows(db_with_schema, "SPY")) == 3
