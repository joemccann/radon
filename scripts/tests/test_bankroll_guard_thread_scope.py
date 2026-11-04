"""Audit 2026-11-03 §3.4: bankroll_guard kept ``_BROKER_BOOK`` and
``_STORE_CLOSE_PROVEN`` as module globals, which assumes one order per
interpreter. In a threaded caller (radon-api's worker pool, the monitor
daemon's handler threads) one thread's broker book replaced another's store
measurement, and one thread's proven close was taken by another.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bankroll_guard  # noqa: E402


def _snapshot_holding(shares: float) -> dict:
    return {
        "positions": [{"ticker": "TQQQ", "legs": [
            {"type": "Stock", "direction": "LONG", "contracts": shares},
        ]}],
    }


SELL_100 = {"type": "stock", "symbol": "TQQQ", "action": "SELL", "quantity": 100}


def _in_other_thread(work):
    """Run ``work(entered, release)`` on a thread; return once it is inside."""
    entered, release = threading.Event(), threading.Event()
    thread = threading.Thread(target=work, args=(entered, release))
    thread.start()
    assert entered.wait(timeout=2)
    return thread, release


def test_another_threads_broker_book_does_not_replace_the_store_measurement():
    def hold_unprovable_book(entered, release):
        with bankroll_guard.broker_book(None):
            entered.set()
            release.wait(timeout=2)

    thread, release = _in_other_thread(hold_unprovable_book)
    try:
        assert bankroll_guard.is_close_out(
            SELL_100, _snapshot_holding(100), working_orders=[]
        ) is True
    finally:
        release.set()
        thread.join(timeout=2)


def test_a_close_proven_on_one_thread_is_not_taken_by_another():
    bankroll_guard.take_store_close_proven()

    def prove_a_close(entered, release):
        assert bankroll_guard.is_close_out(
            SELL_100, _snapshot_holding(100), working_orders=[]
        )
        entered.set()
        release.wait(timeout=2)

    thread, release = _in_other_thread(prove_a_close)
    try:
        assert bankroll_guard.take_store_close_proven() is False
    finally:
        release.set()
        thread.join(timeout=2)


def test_same_thread_semantics_are_unchanged():
    bankroll_guard.take_store_close_proven()
    assert bankroll_guard.is_close_out(SELL_100, _snapshot_holding(100), working_orders=[])
    assert bankroll_guard.take_store_close_proven() is True
    assert bankroll_guard.take_store_close_proven() is False

    with bankroll_guard.broker_book(None):
        assert bankroll_guard.is_close_out(
            SELL_100, _snapshot_holding(100), working_orders=[]
        ) is False
    assert bankroll_guard.take_store_close_proven() is False
