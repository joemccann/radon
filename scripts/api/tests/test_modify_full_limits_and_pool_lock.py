"""REL-059 / R-145 + R-148 (both P1).

R-145: `/orders/modify` enforces only the contract-quantity cap.
`check_quantity_limit` hardcodes `{"type": "option", "quantity": q,
"limitPrice": 0}`, so `order_notional()` returns None (premium is 0) and
`combo_max_loss()` returns None (type is not "combo") — both the notional and
the max-loss branches are skipped, and `newPrice` is bounded only by `> 0`.
Modifying a working 1-lot BAG with a short 195 put leg to `newQuantity: 500`
is accepted and the assignment exposure the PLACE path would have measured is
never computed. REL-005 named modify as a chokepoint for max qty AND max
notional; only qty landed.

R-148: `_PoolContext.__aenter__` takes the role lock and then awaits
`_is_live` and `_reconnect`. `release()` appears only on the three explicit
error branches — no try/finally, no `except BaseException`. A `CancelledError`
at either await (request-task cancellation, an enclosing `wait_for`, shutdown)
propagates with the lock still held. `asyncio.Lock` has no owner and no
timeout, so every later `pool.acquire(role)` blocks forever — on `orders` and
`sync` that is the money path, and no watchdog covers it because
`is_connected` is still true.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent.parent
for p in (str(REPO_ROOT), str(API_DIR.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from api.ib_pool import IBPool, _PoolContext  # noqa: E402


class TestPoolLockIsAlwaysReleased:
    @pytest.mark.asyncio
    async def test_cancellation_during_the_liveness_probe_frees_the_lock(self):
        pool = IBPool()

        async def _hang(_role):
            await asyncio.sleep(30)
            return True

        pool._is_live = _hang  # type: ignore[assignment]

        task = asyncio.create_task(_PoolContext(pool, "orders").__aenter__())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not pool._locks["orders"].locked(), (
            "a cancelled acquire left the role lock held forever — every later "
            "acquire('orders') blocks and only a radon-api restart clears it"
        )

    @pytest.mark.asyncio
    async def test_cancellation_during_reconnect_frees_the_lock(self):
        pool = IBPool()

        async def _dead(_role):
            return False

        async def _hang(_role):
            await asyncio.sleep(30)
            return True

        pool._is_live = _dead  # type: ignore[assignment]
        pool._reconnect = _hang  # type: ignore[assignment]

        task = asyncio.create_task(_PoolContext(pool, "sync").__aenter__())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert not pool._locks["sync"].locked()

    @pytest.mark.asyncio
    async def test_an_unexpected_exception_frees_the_lock(self):
        pool = IBPool()

        async def _boom(_role):
            raise RuntimeError("probe blew up")

        pool._is_live = _boom  # type: ignore[assignment]

        with pytest.raises(RuntimeError):
            await _PoolContext(pool, "data").__aenter__()

        assert not pool._locks["data"].locked()

    @pytest.mark.asyncio
    async def test_the_happy_path_still_holds_the_lock(self):
        pool = IBPool()
        sentinel = object()

        async def _live(_role):
            return True

        pool._is_live = _live  # type: ignore[assignment]
        pool.get = lambda _role: sentinel  # type: ignore[assignment]

        client = await _PoolContext(pool, "orders").__aenter__()
        assert client is sentinel
        assert pool._locks["orders"].locked()


class TestModifyEnforcesTheFullLimitSet:
    def test_a_modify_is_measured_with_the_working_orders_shape(self):
        """The route holds `new_price` and can read the working order's type
        and legs from the snapshot it already fetches, so the notional and
        max-loss branches can run exactly as the place path runs them."""
        from order_limits import check_modify_limits

        # A risk reversal: the short 195 put is NOT covered by the long call,
        # so 500 lots is 500 x 195 x 100 = $9.75M of assignment-to-zero — the
        # exposure the place path measures and modify never did.
        working_bag = {
            "orderType": "LMT",
            "secType": "BAG",
            "legs": [
                {"action": "SELL", "right": "P", "strike": 195.0, "ratio": 1},
                {"action": "BUY", "right": "C", "strike": 205.0, "ratio": 1},
            ],
        }
        violation = check_modify_limits(
            working_bag, new_quantity=500, new_price=1.00, action="BUY"
        )
        assert violation is not None, (
            "a 1-lot BAG modified to 500 lots skipped both the notional and "
            "the max-loss branch"
        )
        assert violation["code"] in {
            "ORDER_MAX_LOSS_LIMIT",
            "ORDER_NOTIONAL_LIMIT",
            "ORDER_EFFECTIVE_QTY_LIMIT",
        }

    def test_a_price_only_modify_is_bounded_by_notional(self):
        from order_limits import check_modify_limits

        working = {"orderType": "LMT", "secType": "OPT", "quantity": 100}
        assert check_modify_limits(working, new_quantity=None, new_price=5.0) is None
        violation = check_modify_limits(working, new_quantity=None, new_price=400.0)
        assert violation is not None, "newPrice is still bounded only by > 0"
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"

    def test_an_ordinary_modify_still_passes(self):
        from order_limits import check_modify_limits

        working = {"orderType": "LMT", "secType": "OPT", "quantity": 5}
        assert check_modify_limits(working, new_quantity=10, new_price=12.0) is None

    def test_a_defined_risk_combo_resize_is_not_refused_on_assignment(self):
        """Control: the long leg covers the short one, so 500 lots of a
        5-wide put spread is $250k of DEFINED risk, not $9.75M."""
        from order_limits import check_modify_limits

        spread = {
            "orderType": "LMT",
            "secType": "BAG",
            "legs": [
                {"action": "SELL", "right": "P", "strike": 195.0, "ratio": 1},
                {"action": "BUY", "right": "P", "strike": 190.0, "ratio": 1},
            ],
        }
        assert check_modify_limits(
            spread, new_quantity=500, new_price=1.00, action="BUY"
        ) is None

    def test_an_unreadable_working_order_still_applies_the_quantity_cap(self):
        """No snapshot row (a just-placed order, a Turso blip) must not
        become a bypass — the old contract-quantity cap still runs."""
        from order_limits import check_modify_limits

        violation = check_modify_limits(None, new_quantity=100_000, new_price=1.0)
        assert violation is not None
        assert violation["code"] == "ORDER_QTY_LIMIT"
