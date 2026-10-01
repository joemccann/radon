"""The CREDIT/VIX broker leg executes against a fake IB, never a socket."""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import ib_insync
import pytest

import fetch_credit_vix as mod


@pytest.fixture
def broker(monkeypatch):
    bars = [
        SimpleNamespace(date=date(2026, 9, 29), close=16.04),
        SimpleNamespace(date="2026-09-30", close=17.25),
        SimpleNamespace(date=date(2026, 10, 1), close=None),
    ]
    ib = SimpleNamespace(
        qualifyContractsAsync=AsyncMock(side_effect=lambda contract: [contract]),
        reqHistoricalDataAsync=AsyncMock(return_value=bars),
        disconnect=Mock(),
        run=asyncio.run,
    )
    factory = Mock(return_value=ib)
    connect = Mock(return_value=True)
    monkeypatch.setattr(ib_insync, "IB", factory)
    monkeypatch.setattr(mod, "_connect_ib_with_retry", connect)
    monkeypatch.setattr(mod, "_ib_auth_state", lambda: "authenticated")
    # Run the coroutine normally; record the actual wait_for timeout argument.
    wait_for = Mock(wraps=asyncio.wait_for)
    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    return SimpleNamespace(ib=ib, factory=factory, connect=connect, wait_for=wait_for)


def test_index_contract_and_daily_request_reach_the_broker(broker):
    assert mod.fetch_ib_vix() == {"2026-09-29": 16.04, "2026-09-30": 17.25}
    contract = broker.ib.qualifyContractsAsync.call_args.args[0]
    assert (contract.secType, contract.symbol, contract.exchange) == ("IND", "VIX", "CBOE")
    broker.connect.assert_called_once_with(broker.ib, client_ids=(56, 69))
    broker.ib.reqHistoricalDataAsync.assert_awaited_once_with(
        contract, endDateTime="", durationStr="1 Y", barSizeSetting="1 day",
        whatToShow="TRADES", useRTH=True, formatDate=1,
    )
    assert [call.kwargs['timeout'] for call in broker.wait_for.call_args_list] == [
        mod.IB_REQUEST_TIMEOUT_S, mod.IB_HISTORICAL_TIMEOUT_S,
    ]
    broker.ib.disconnect.assert_called_once_with()


@pytest.mark.parametrize('state', ['awaiting_2fa', 'disconnected'])
def test_unauthenticated_gateway_constructs_no_broker(broker, monkeypatch, state):
    monkeypatch.setattr(mod, '_ib_auth_state', lambda: state)
    assert mod.fetch_ib_vix() == {}
    broker.factory.assert_not_called()
    broker.connect.assert_not_called()


@pytest.mark.parametrize('state', [None, ''])
def test_unknown_gateway_state_still_attempts_ib(broker, monkeypatch, state):
    monkeypatch.setattr(mod, '_ib_auth_state', lambda: state)
    assert mod.fetch_ib_vix()['2026-09-30'] == 17.25
    broker.connect.assert_called_once()
    broker.ib.disconnect.assert_called_once()


def test_connection_refusal_never_requests_history(broker):
    broker.connect.return_value = False
    assert mod.fetch_ib_vix() == {}
    broker.ib.qualifyContractsAsync.assert_not_awaited()
    broker.ib.reqHistoricalDataAsync.assert_not_awaited()


def test_optional_driver_unavailable_allows_fallback(broker, monkeypatch):
    monkeypatch.setitem(sys.modules, 'ib_insync', None)
    assert mod.fetch_ib_vix() == {}
    broker.factory.assert_not_called()


@pytest.mark.parametrize('error', [TimeoutError('qualification timeout'), RuntimeError('invalid contract')])
def test_qualification_failure_disconnects_and_allows_fallback(broker, error):
    broker.ib.qualifyContractsAsync.side_effect = error
    assert mod.fetch_ib_vix() == {}
    broker.ib.reqHistoricalDataAsync.assert_not_awaited()
    broker.ib.disconnect.assert_called_once()


def test_empty_qualification_never_requests_unqualified_history(broker):
    broker.ib.qualifyContractsAsync.side_effect = None
    broker.ib.qualifyContractsAsync.return_value = []
    assert mod.fetch_ib_vix() == {}
    broker.ib.reqHistoricalDataAsync.assert_not_awaited()
    broker.ib.disconnect.assert_called_once()


def test_in_place_qualified_contract_is_accepted(broker):
    def qualify(contract):
        contract.conId = 123
        return []
    broker.ib.qualifyContractsAsync.side_effect = qualify
    assert mod.fetch_ib_vix()['2026-09-29'] == 16.04
    broker.ib.reqHistoricalDataAsync.assert_awaited_once()
    broker.ib.disconnect.assert_called_once()


@pytest.mark.parametrize('error', [TimeoutError('history timeout'), RuntimeError('history rejected')])
def test_history_failure_disconnects_and_allows_fallback(broker, error):
    broker.ib.reqHistoricalDataAsync.side_effect = error
    assert mod.fetch_ib_vix() == {}
    broker.ib.disconnect.assert_called_once()


@pytest.mark.parametrize('bars', [None, []])
def test_empty_history_returns_no_data_and_disconnects(broker, bars):
    broker.ib.reqHistoricalDataAsync.return_value = bars
    assert mod.fetch_ib_vix() == {}
    broker.ib.disconnect.assert_called_once()
