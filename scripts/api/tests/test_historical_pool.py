"""Tests for historical endpoint ib_pool resolution and bounded calls."""

import asyncio
import threading
import pytest
from unittest.mock import MagicMock

from fastapi import HTTPException
from pydantic import ValidationError
from api.routes.historical import (
    ContractSpec,
    QualifyRequest,
    _bounded_pool_call,
    _get_pool,
)


class FakeState:
    pass


class FakeApp:
    def __init__(self, pool=None):
        self.state = FakeState()
        if pool is not None:
            self.state.ib_pool = pool


class FakeRequest:
    def __init__(self, app):
        self.app = app


class TestGetPool:
    def test_returns_pool_from_app_state(self):
        mock_pool = MagicMock()
        request = FakeRequest(FakeApp(pool=mock_pool))
        assert _get_pool(request) is mock_pool

    def test_raises_503_when_pool_is_none(self):
        request = FakeRequest(FakeApp(pool=None))
        with pytest.raises(HTTPException) as exc_info:
            _get_pool(request)
        assert exc_info.value.status_code == 503


def test_qualify_contract_batch_is_bounded():
    contracts = [ContractSpec(symbol=f"S{i}") for i in range(51)]
    with pytest.raises(ValidationError):
        QualifyRequest(contracts=contracts)


def test_timed_out_ib_call_retires_client_without_releasing_live_worker():
    started = threading.Event()
    release = threading.Event()
    retired = []
    client = MagicMock()

    class Pool:
        async def retire(self, role, retiring):
            retired.append((role, retiring))

    def wedged():
        started.set()
        release.wait(timeout=2)

    async def scenario():
        with pytest.raises(HTTPException) as exc:
            await _bounded_pool_call(Pool(), "data", client, wedged, timeout=0.01)
        assert exc.value.status_code == 504
        assert started.is_set()
        assert retired == [("data", client)]
        release.set()
        await asyncio.sleep(0.02)

    asyncio.run(scenario())

    def test_raises_503_when_pool_not_set(self):
        request = FakeRequest(FakeApp())  # no ib_pool on state
        with pytest.raises(HTTPException) as exc_info:
            _get_pool(request)
        assert exc_info.value.status_code == 503
