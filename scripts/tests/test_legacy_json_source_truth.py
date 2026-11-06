"""Turso and IB are the sources of truth; retired JSON files must stay retired.

On 2026-11-06 production's data/portfolio.json was frozen at 2026-06-23 with a
$1.53M bankroll and 12 positions, against a live $573k and 5. The Kelly guard and
evaluate M6 still read it, and option_session_mark_cache.json froze IWO 380C at
1.95. The earlier contract only covered a hand-kept list of scripts.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

import bankroll_guard
import evaluate
import kelly_guard
import option_session_mark

ROOT = Path(__file__).resolve().parents[2]
RETIRED = re.compile(
    r"(?<![\w-])(portfolio|trade_log|orders|blotter|reconciliation|option_session_mark_cache)\.json\b"
)
SCANNED = ("scripts", "web/app", "web/lib", "web/components", "cloud/scripts", "lib")
SUFFIXES = {".py", ".js", ".mjs", ".ts", ".tsx", ".sh"}
EXCLUDED_PARTS = {"tests", "__tests__", "node_modules", ".next"}


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith(("#", "//", "*", "/*"))


def _offenders() -> list[str]:
    found = []
    for tree in SCANNED:
        for path in (ROOT / tree).rglob("*"):
            if path.suffix not in SUFFIXES or EXCLUDED_PARTS & set(path.parts) or not path.is_file():
                continue
            for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if RETIRED.search(line) and not _is_comment(line):
                    found.append(f"{path.relative_to(ROOT)}:{number}")
    return found


def test_no_runtime_code_names_a_retired_json_file() -> None:
    assert _offenders() == []


SNAPSHOT = {
    "bankroll": 573_523.66,
    "account_summary": {"net_liquidation": 573_523.66},
    "positions": [{"max_risk": 25_401.83}, {"max_risk": None}, {"max_risk": 70_254.9}],
}


def test_kelly_bankroll_comes_from_the_latest_turso_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: SNAPSHOT)
    assert kelly_guard.resolve_kelly_bankroll() == 573_523.66


def test_kelly_bankroll_is_unknown_when_turso_is_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: None)
    assert kelly_guard.resolve_kelly_bankroll() is None


def test_evaluate_open_losses_come_from_the_latest_turso_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: SNAPSHOT)
    assert evaluate._open_max_losses_from_portfolio() == [25_401.83, 70_254.9]


def test_evaluate_open_losses_are_unknown_when_turso_is_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: None)
    assert evaluate._open_max_losses_from_portfolio() is None


@pytest.fixture
def turso(monkeypatch):
    """The real migration on sqlite behind the Hrana read and write seams."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schema_migrations(version INTEGER, applied_at TEXT)")
    migration = next((ROOT / "scripts/db/migrations").glob("*_option_session_marks.sql"))
    conn.executescript(migration.read_text())

    def execute(sql, args=(), **_kwargs):
        return conn.execute(sql, tuple(args)).fetchall()

    monkeypatch.setattr(option_session_mark, "_read_rows", execute)
    monkeypatch.setattr(option_session_mark, "_write", execute)
    return conn


KEY = "IWO_20261218_380_C"
MARK = {"last": 1.95, "bid": 0.25, "ask": 1.0, "checked_on": "2026-11-06"}


def test_session_marks_round_trip_through_turso(turso) -> None:
    option_session_mark.save_session_marks({KEY: MARK})
    assert option_session_mark.load_session_marks([KEY]) == {KEY: MARK}


def test_session_mark_load_is_scoped_to_the_requested_keys(turso) -> None:
    option_session_mark.save_session_marks({KEY: MARK, "META_20261016_665_P": MARK})
    assert set(option_session_mark.load_session_marks([KEY])) == {KEY}


def test_session_mark_save_replaces_the_row(turso) -> None:
    option_session_mark.save_session_marks({KEY: MARK})
    option_session_mark.save_session_marks({KEY: {"mid": 1.2, "checked_on": "2026-11-07"}})
    assert option_session_mark.load_session_marks([KEY]) == {KEY: {"mid": 1.2, "checked_on": "2026-11-07"}}


def test_session_mark_does_not_persist_close_as_last(turso) -> None:
    option_session_mark.save_session_marks({KEY: {"last": 0, "close": 26.7}})
    assert option_session_mark.load_session_marks([KEY]) == {}


def test_session_mark_load_without_keys_reads_nothing(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("no keys, no read")

    monkeypatch.setattr(option_session_mark, "_read_rows", fail)
    assert option_session_mark.load_session_marks([]) == {}


def test_session_mark_turso_failure_is_an_empty_cache_not_a_failed_sync(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise ConnectionError("turso 502")

    monkeypatch.setattr(option_session_mark, "_read_rows", fail)
    monkeypatch.setattr(option_session_mark, "_write", fail)
    assert option_session_mark.load_session_marks([KEY]) == {}
    option_session_mark.save_session_marks({KEY: MARK})
