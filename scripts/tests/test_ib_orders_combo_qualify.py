"""fetch_open_orders qualifies BAG leg conIds in one qualify_contracts call."""
from __future__ import annotations

from types import SimpleNamespace

_LEG_CON_IDS = (101, 202, 303)


def _resolved(con_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        conId=con_id,
        symbol="SPY",
        secType="OPT",
        strike=float(con_id),
        right="C",
        lastTradeDateOrContractMonth="20261218",
    )


def _bag_trade(con_ids=_LEG_CON_IDS) -> SimpleNamespace:
    return SimpleNamespace(
        contract=SimpleNamespace(
            conId=9001,
            symbol="SPY",
            secType="BAG",
            strike=0.0,
            right="",
            lastTradeDateOrContractMonth="",
            currency="USD",
            multiplier="",
            localSymbol="",
            tradingClass="",
            comboLegs=[
                SimpleNamespace(conId=con_id, ratio=1, action="BUY")
                for con_id in con_ids
            ],
        ),
        order=SimpleNamespace(
            orderId=7,
            permId=1007,
            orderRef="",
            action="BUY",
            orderType="LMT",
            totalQuantity=1,
            lmtPrice=1.25,
            auxPrice=None,
            tif="DAY",
            outsideRth=False,
        ),
        orderStatus=SimpleNamespace(
            status="Submitted",
            filled=0,
            remaining=1,
            avgFillPrice=0,
        ),
    )


def _client(trades, calls, *, batch_raises=False, raise_con_ids=()):
    failing = set(raise_con_ids)

    def qualify_contracts(*contracts):
        calls.append(contracts)
        if len(contracts) != 1:
            if batch_raises:
                raise RuntimeError("batch qualify failed")
            return [_resolved(contract.conId) for contract in contracts]
        con_id = contracts[0].conId
        if con_id in failing:
            raise RuntimeError(f"unresolvable {con_id}")
        return [_resolved(con_id)]

    return SimpleNamespace(
        get_open_orders=lambda: trades,
        qualify_contracts=qualify_contracts,
    )


def _stored_con_ids(row) -> set[int]:
    return {
        leg["conId"]
        for leg in row["contract"]["comboLegs"]
        if "strike" in leg
    }


def test_fetch_open_orders_qualifies_combo_legs_in_one_call():
    import ib_orders

    calls = []
    client = _client([_bag_trade()], calls)
    rows = ib_orders.fetch_open_orders(client)

    assert len(calls) == 1
    assert {contract.conId for contract in calls[0]} == set(_LEG_CON_IDS)
    assert _stored_con_ids(rows[0]) == set(_LEG_CON_IDS)
    stored = {leg["conId"]: leg for leg in rows[0]["contract"]["comboLegs"]}
    assert stored[101]["strike"] == 101.0
    assert stored[202]["strike"] == 202.0
    assert stored[303]["strike"] == 303.0


def test_fetch_open_orders_falls_back_to_per_leg_when_batch_raises():
    import ib_orders

    calls = []
    client = _client(
        [_bag_trade()],
        calls,
        batch_raises=True,
        raise_con_ids=(202,),
    )
    rows = ib_orders.fetch_open_orders(client)

    assert len(calls) == 1 + len(_LEG_CON_IDS)
    assert {contract.conId for contract in calls[0]} == set(_LEG_CON_IDS)
    per_leg = calls[1:]
    assert all(len(call) == 1 for call in per_leg)
    assert {call[0].conId for call in per_leg} == set(_LEG_CON_IDS)
    stored = {leg["conId"]: leg for leg in rows[0]["contract"]["comboLegs"]}
    assert stored[101]["strike"] == 101.0
    assert stored[303]["strike"] == 303.0
    assert "strike" not in stored[202]
    assert "secType" not in stored[202]
