"""DS-2026-10-24-02 residual: concurrent closes cannot spend the same size.

The placement funnel re-admits a store-proven close against the broker's live
book before it transmits, which closed the fill-then-close and sequential
cases. Two placement subprocesses still raced inside the window between that
live-book read and ``placeOrder``: both read a book in which the holding was
unreserved, both were exempted from the loss cap, and the surplus of the
second opened an uncapped position. ``/orders/place`` runs one subprocess per
order, so an in-process lock cannot see the contention; the claim has to be
exclusive across processes.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import eventkit
import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bankroll_guard  # noqa: E402
from bankroll_guard import check_bankroll_admission as _real_bankroll_admission  # noqa: E402

NOW = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
STOCK = SimpleNamespace(symbol="TQQQ", secType="STK", conId=1)

# Holds the claim for TQQQ until its stdin closes, then exits. Writing the
# lock directory in as an argument keeps the child on the test's own tree.
_HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    import bankroll_guard
    bankroll_guard.CLOSE_ADMISSION_LOCK_DIR = Path(sys.argv[2])
    with bankroll_guard.close_admission_claim(sys.argv[3], timeout=5.0) as held:
        print("held" if held else "free", flush=True)
        sys.stdin.read()
    """
)


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    target = tmp_path / "close_admission"
    monkeypatch.setattr(bankroll_guard, "CLOSE_ADMISSION_LOCK_DIR", target)
    return target


@pytest.fixture
def holder(lock_dir):
    """A real second process holding the TQQQ claim for the test's duration."""
    procs = []

    def start(symbol="TQQQ"):
        proc = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, str(_SCRIPTS), str(lock_dir), symbol],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        procs.append(proc)
        assert proc.stdout.readline().strip() == "held"
        return proc

    yield start
    for proc in procs:
        proc.stdin.close()
        proc.wait(timeout=10)


def _snapshot(shares, *, stale=True):
    synced = NOW - timedelta(minutes=30 if stale else 1)
    return {
        "last_sync": synced.isoformat(),
        "account_summary": {"net_liquidation": 100_000.0},
        "positions": [{"ticker": "TQQQ", "legs": [
            {"type": "Stock", "direction": "LONG", "contracts": shares},
        ]}],
    }


def _sell(quantity=100):
    return {"type": "stock", "symbol": "TQQQ", "action": "SELL", "quantity": quantity,
            "limitPrice": 60.0, "tif": "DAY"}


def _client(broker_shares):
    trade = MagicMock()
    trade.order.orderId = 99
    trade.order.permId = 12345
    trade.orderStatus.status = "Submitted"
    trade.orderStatus.whyHeld = ""
    trade.log = []
    client = MagicMock()
    client._ib = MagicMock()
    client._ib.errorEvent = eventkit.Event("errorEvent")
    client.place_order = MagicMock(return_value=trade)
    client.qualify_contracts = MagicMock(return_value=[MagicMock(conId=1)])
    client.get_positions = MagicMock(return_value=(
        [SimpleNamespace(contract=STOCK, position=broker_shares)] if broker_shares else []
    ))
    client.get_open_orders = MagicMock(return_value=[])
    return client


@pytest.fixture(autouse=True)
def _enforced(monkeypatch):
    import app_preferences

    app_preferences.seed_snapshot_for_tests({"RADON_BANKROLL_CAP_ENFORCE_ALL_PATHS": "true"})
    monkeypatch.setattr(bankroll_guard, "check_bankroll_admission", _real_bankroll_admission)
    monkeypatch.setattr(bankroll_guard, "_utcnow", lambda: NOW)
    monkeypatch.setattr(bankroll_guard, "_load_working_orders", lambda: [])
    monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: _snapshot(100))
    yield
    app_preferences.clear_snapshot_for_tests()


def _place(params, client):
    with patch("ib_place_order.IBClient", return_value=client), \
         patch("ib_place_order.Stock", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()):
        import ib_place_order

        return ib_place_order.place_order(params)


class TestTheClaimIsExclusiveAcrossProcesses:
    def test_a_claim_another_process_holds_cannot_be_taken(self, lock_dir, holder):
        holder("TQQQ")

        with bankroll_guard.close_admission_claim("TQQQ", timeout=0.5) as held:
            assert held is False

    def test_a_claim_on_another_symbol_is_independent(self, lock_dir, holder):
        holder("TQQQ")

        with bankroll_guard.close_admission_claim("SPY", timeout=0.5) as held:
            assert held is True

    def test_the_claim_is_released_when_the_holder_exits(self, lock_dir, holder):
        proc = holder("TQQQ")
        proc.stdin.close()
        proc.wait(timeout=10)

        with bankroll_guard.close_admission_claim("TQQQ", timeout=5.0) as held:
            assert held is True

    def test_the_symbol_is_matched_after_normalisation(self, lock_dir, holder):
        holder("TQQQ")

        with bankroll_guard.close_admission_claim(" tqqq ", timeout=0.5) as held:
            assert held is False


class TestAConcurrentCloseCannotSpendTheSameSize:
    def test_a_close_racing_another_placer_never_transmits(self, lock_dir, holder):
        holder("TQQQ")
        client = _client(broker_shares=100)

        result = _place(_sell(100), client)

        assert result["status"] == "error", result
        assert result.get("code") == "CLOSE_ADMISSION_BUSY", result
        client.place_order.assert_not_called()

    def test_an_uncontended_close_still_transmits(self, lock_dir):
        client = _client(broker_shares=100)

        result = _place(_sell(100), client)

        assert result["status"] == "ok", result
        client.place_order.assert_called_once()

    def test_the_claim_is_released_for_the_next_close(self, lock_dir):
        first = _client(broker_shares=100)
        assert _place(_sell(100), first)["status"] == "ok"

        second = _client(broker_shares=100)
        result = _place(_sell(100), second)

        assert result["status"] == "ok", result
        second.place_order.assert_called_once()

    def test_a_contended_order_that_is_no_close_still_transmits(self, lock_dir, holder, monkeypatch):
        """Serialisation is scoped to the close exemption, not to all placement."""
        holder("TQQQ")
        monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot",
                            lambda: _snapshot(0, stale=False))
        client = _client(broker_shares=0)
        buy = {"type": "stock", "symbol": "TQQQ", "action": "BUY", "quantity": 1,
               "limitPrice": 60.0, "tif": "DAY"}

        result = _place(buy, client)

        assert result["status"] == "ok", result
        client.place_order.assert_called_once()
