"""T-033 — confirm-poll deadline budget pin (scripts/ib_place_order.py:385).

    deadline = _clock() + (12.0 if order_type == "combo" else 6.0)

Combo orders get 12s to confirm; single-leg orders get 6s. Per
scripts/CLAUDE.md's Order Placement Contract §1: "Combos get the longer
deadline because IB risk-checks each leg independently AND the SmartRouting
+ NonGuaranteed handshake adds ~3-5s on top of single-leg latency." This is a
regression pin, not a functional fix — TEST-ONLY, no fix diff.

Uses the injectable `_clock` parameter (test_spx01_grace_wait.py's harness:
same trick as its test_no_grace_wait_on_perm_id_zero_limbo, which drives an
already-expired clock to skip real wall-clock waiting). The deadline itself
is a local variable never returned to the caller, so the only externally
observable trace of its width is how many times `_clock()` gets called
before the confirm-poll loop's `_clock() < deadline` check goes false.
Reconstructing the width from the recorded call values (last - first) is
independent of the real 0.5s `client.sleep` interval and of exactly how many
loop iterations that implies — the assertion fails if and only if the
deadline width itself changes, which is the "least implementation-coupled"
option of the two the task offered (poll-counting vs. capturing the deadline
through the clock calls).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import eventkit

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# Helpers — mirrors test_spx01_grace_wait.py's fixture style
# ---------------------------------------------------------------------------

def _make_order(order_id: int = 99, perm_id: int = 0) -> MagicMock:
    o = MagicMock()
    o.orderId = order_id
    o.permId = perm_id
    return o


def _make_order_status(status: str) -> MagicMock:
    os = MagicMock()
    os.status = status
    os.whyHeld = ""
    return os


def _make_trade(status: str = "PendingSubmit", perm_id: int = 0) -> MagicMock:
    """A trade that NEVER satisfies a poll-loop break condition: permId stays
    0, status stays non-terminal, and no error ever arrives. The only way
    the confirm-poll loop can exit is the `_clock() < deadline` check going
    false — which is exactly what these tests measure.
    """
    trade = MagicMock()
    trade.order = _make_order(order_id=99, perm_id=perm_id)
    trade.orderStatus = _make_order_status(status=status)
    trade.log = []
    return trade


def _make_client(trade: MagicMock) -> MagicMock:
    client = MagicMock()

    real_error_event = eventkit.Event("errorEvent")
    ib_mock = MagicMock()
    ib_mock.errorEvent = real_error_event
    client._ib = ib_mock

    client.place_order = MagicMock(return_value=trade)
    # Combo legs qualify N-for-N; single-leg qualifies 1-for-1 — place_order
    # bails early if len(qualified) != len(options), so the mock must track
    # the number of contracts it was asked to qualify.
    client.qualify_contracts = MagicMock(
        side_effect=lambda *contracts: [MagicMock(conId=1000 + i) for i in range(len(contracts))]
    )
    client.sleep = MagicMock()
    client.disconnect = MagicMock()
    return client


def _make_recording_clock(start: float = 1_000_000.0, step: float = 0.05):
    """Zero-arg clock that advances by `step` seconds each call and records
    every value it returns, so the confirm-poll deadline width can be
    reconstructed from `calls[-1] - calls[0]` after the loop exits.
    """
    calls: list[float] = []

    def _clock() -> float:
        value = start + step * len(calls)
        calls.append(value)
        return value

    _clock.calls = calls
    return _clock


def _invoke_place_order(params: dict, client_mock: MagicMock, _clock) -> dict:
    with patch("ib_place_order.IBClient", return_value=client_mock), \
         patch("ib_place_order.Stock", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()):
        import ib_place_order
        return ib_place_order.place_order(params, _clock=_clock)


_SINGLE_LEG_PARAMS = {
    "type": "stock",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 100,
    "limitPrice": 214.50,
    "tif": "DAY",
}

_COMBO_PARAMS = {
    "type": "combo",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 1,
    "limitPrice": 1.50,
    "tif": "DAY",
    "legs": [
        {"expiry": "20260417", "strike": 200, "right": "C", "action": "BUY", "ratio": 1},
        {"expiry": "20260417", "strike": 210, "right": "C", "action": "SELL", "ratio": 1},
    ],
}


# ---------------------------------------------------------------------------
# T-033 Tests
# ---------------------------------------------------------------------------

class TestConfirmPollDeadlineBudget:
    def test_combo_confirm_deadline_is_12_seconds(self):
        clock = _make_recording_clock(step=0.05)
        client = _make_client(_make_trade())

        _invoke_place_order(_COMBO_PARAMS, client, _clock=clock)

        observed_width = clock.calls[-1] - clock.calls[0]
        assert 11.5 <= observed_width <= 12.5, (
            f"combo confirm-poll deadline should be ~12.0s wide, "
            f"observed {observed_width:.2f}s over {len(clock.calls)} clock() calls"
        )

    def test_single_leg_confirm_deadline_is_6_seconds(self):
        clock = _make_recording_clock(step=0.05)
        client = _make_client(_make_trade())

        _invoke_place_order(_SINGLE_LEG_PARAMS, client, _clock=clock)

        observed_width = clock.calls[-1] - clock.calls[0]
        assert 5.5 <= observed_width <= 6.5, (
            f"single-leg confirm-poll deadline should be ~6.0s wide, "
            f"observed {observed_width:.2f}s over {len(clock.calls)} clock() calls"
        )
