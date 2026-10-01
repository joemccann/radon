"""REL-021b / R-039: a visible order is not owned by the pool connection."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api import pool_order_manage as mod


@pytest.fixture(autouse=True)
def _not_halted(monkeypatch):
    import trading_halt
    monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: False)


def trade():
    return SimpleNamespace(
        order=SimpleNamespace(orderId=42, permId=4242, clientId=26,
                              lmtPrice=5.0, totalQuantity=1, orderType='LMT',
                              outsideRth=False),
        contract=SimpleNamespace(secType='STK'),
        orderStatus=SimpleNamespace(status='Submitted'),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['cancel', 'modify'])
@pytest.mark.parametrize('client_id', [0, 3, None, '26'])
async def test_other_or_unknown_client_cannot_mutate_order(operation, client_id):
    item = trade()
    client = MagicMock()
    client.ib.client.clientId = client_id
    client.get_open_orders.side_effect = [[item], []]
    function = getattr(mod, f'pool_{operation}_order')
    fields = {'new_price': 6.0} if operation == 'modify' else {}
    result = await function(client, order_id=42, max_wait=0, **fields)
    assert result['status'] == 'error'
    assert result.get('code') == 'ORDER_CLIENT_MISMATCH'
    assert result.get('orderId') == 42
    client.cancel_order.assert_not_called()
    client.place_order.assert_not_called()
    assert item.order.lmtPrice == 5.0


@pytest.mark.asyncio
async def test_halted_owner_cannot_modify(monkeypatch):
    import trading_halt
    monkeypatch.setattr(trading_halt, 'is_trading_halted', lambda: True)
    monkeypatch.setattr(trading_halt, 'get_halt_state', lambda: {'reason': 'fixture'})
    item = trade()
    client = MagicMock()
    client.ib.client.clientId = 26
    client.get_open_orders.return_value = [item]
    result = await mod.pool_modify_order(client, order_id=42, new_price=6.0, max_wait=0)
    assert result.get('code') == 'TRADING_HALTED'
    client.place_order.assert_not_called()
    assert item.order.lmtPrice == 5.0


def test_server_does_not_import_the_unused_pooled_shortcut():
    source = Path(__file__).resolve().parents[1] / 'api' / 'server.py'
    tree = ast.parse(source.read_text())
    assert not any(isinstance(node, ast.ImportFrom) and
                   (node.module or '').endswith('pool_order_manage')
                   for node in ast.walk(tree))


@pytest.mark.asyncio
async def test_halt_does_not_prevent_owned_cancellation(monkeypatch):
    import trading_halt
    monkeypatch.setattr(trading_halt, 'is_trading_halted', lambda: True)
    item = trade()
    client = MagicMock()
    client.ib.client.clientId = 26
    client.get_open_orders.side_effect = [[item], []]
    result = await mod.pool_cancel_order(client, order_id=42, max_wait=0.01, poll_interval=0.001)
    assert result['status'] == 'ok'
    client.cancel_order.assert_called_once_with(item.order)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['cancel', 'modify'])
async def test_owned_broker_error_is_not_reported_as_success(operation):
    item = trade()
    client = MagicMock()
    client.ib.client.clientId = 26
    client.get_open_orders.return_value = [item]
    mutation = client.cancel_order if operation == 'cancel' else client.place_order
    mutation.side_effect = RuntimeError('broker refusal fixture')
    fields = {'new_price': 6.0} if operation == 'modify' else {}
    with pytest.raises(RuntimeError, match='broker refusal fixture'):
        await getattr(mod, f'pool_{operation}_order')(client, order_id=42, **fields)
    mutation.assert_called_once()
