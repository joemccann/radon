"""REL-304 / R-723: the broker wire rechecks the halt after caller preflight."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from clients.ib_client import IBClient, IBOrderError
import trading_halt


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(trading_halt, 'HALT_FILE', tmp_path / 'halt.json')
    broker = MagicMock()
    broker.isConnected.return_value = True
    # Construct without IB(), socket allocation, or credential discovery.
    client = object.__new__(IBClient)
    client._ib = broker
    client.logger = MagicMock()
    return client


def request(sec_type='STK'):
    return (SimpleNamespace(symbol='MOCK', secType=sec_type),
            SimpleNamespace(action='BUY', totalQuantity=1, lmtPrice=10, orderType='LMT'))


@pytest.mark.parametrize('method', ['place_order', 'modify_order'])
@pytest.mark.parametrize('sec_type', ['STK', 'OPT', 'BAG'])
def test_halt_after_caller_preflight_refuses_at_wire(client, method, sec_type):
    assert trading_halt.is_trading_halted() is False  # caller admitted earlier
    trading_halt.set_halt(reason='mock incident', actor='test')
    contract, order = request(sec_type)
    with pytest.raises(IBOrderError, match='(?i)halt'):
        getattr(client, method)(contract, order)
    client._ib.placeOrder.assert_not_called()


@pytest.mark.parametrize('method', ['place_order', 'modify_order'])
def test_unreadable_halt_refuses_at_wire(client, method):
    trading_halt.HALT_FILE.write_text('{broken')
    with pytest.raises(IBOrderError, match='(?i)halt'):
        getattr(client, method)(*request())
    client._ib.placeOrder.assert_not_called()


def test_halted_modify_preserves_order_fields(client):
    trading_halt.set_halt(reason='mock incident', actor='test')
    contract, order = request()
    with pytest.raises(IBOrderError, match='(?i)halt'):
        client.modify_order(contract, order, lmt_price=20, total_quantity=2)
    assert (order.lmtPrice, order.totalQuantity) == (10, 1)
    client._ib.placeOrder.assert_not_called()


@pytest.mark.parametrize('method', ['place_order', 'modify_order'])
def test_confirmed_resume_keeps_valid_placement(client, method):
    trading_halt.set_halt(reason='mock incident', actor='test')
    trading_halt.clear_halt(actor='test')
    contract, order = request()
    result = getattr(client, method)(contract, order)
    client._ib.placeOrder.assert_called_once_with(contract, order)
    assert result is client._ib.placeOrder.return_value


def test_cancel_remains_available_during_halt(client):
    trading_halt.set_halt(reason='mock incident', actor='test')
    _, order = request()
    client.cancel_order(order)
    client._ib.cancelOrder.assert_called_once_with(order)
