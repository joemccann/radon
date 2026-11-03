"""IB failure cooldown + placement outcome under recovery (audit §1 #13).

The cooldown used to be armed on ANY connection-pattern error BEFORE the
Gateway health check, and stayed armed when the Gateway was healthy. A slow
read script's timeout then refused order cancel/modify for 15s. It is now
armed only on a confirmed gateway-down verdict, and an order cancel is never
refused on it.

A placement failure whose text marks the outcome indeterminate must keep that
meaning when cloud/managed recovery rewrites the connection error.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from scripts.api import ib_gateway, server
from scripts.api.subprocess import ScriptResult


def _wire(monkeypatch, *, results, gateway):
    calls: list = []

    async def runner(script, args, timeout=30):
        calls.append((script, list(args)))
        return results.pop(0) if results else ScriptResult(ok=True, data={"status": "ok"})

    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "run_script", runner)
    monkeypatch.setattr(server, "check_ib_gateway", AsyncMock(return_value=gateway))
    monkeypatch.setattr(server, "restart_ib_gateway", AsyncMock(side_effect=AssertionError("no restart")))
    monkeypatch.setattr(server, "_pool_has_any_connection", lambda: True)
    monkeypatch.setattr(server, "_ib_last_failure", 0.0)
    monkeypatch.setattr(server, "ib_pool", None)
    monkeypatch.setattr(server, "is_cloud_mode", lambda: True)
    return calls


_HEALTHY = {"port_listening": True, "upstream_dead": False}
_DOWN = {"port_listening": False, "upstream_dead": False}


class TestCooldownArming:
    def test_healthy_gateway_does_not_arm_cooldown(self, monkeypatch):
        calls = _wire(
            monkeypatch,
            results=[ScriptResult(ok=False, error="account updates request timed out")],
            gateway=_HEALTHY,
        )
        first = asyncio.run(server._run_ib_script_with_recovery("ib_sync.py", []))
        assert first.ok is False
        # The Gateway answered healthy: the next order action must still run.
        second = asyncio.run(server._run_ib_script_with_recovery(
            "ib_order_manage.py", ["modify", "--perm-id", "7", "--new-price", "1.0"],
        ))
        assert second.ok is True
        assert [c[0] for c in calls] == ["ib_sync.py", "ib_order_manage.py"]

    def test_confirmed_down_arms_cooldown(self, monkeypatch):
        calls = _wire(
            monkeypatch,
            results=[ScriptResult(ok=False, error="Connect call failed")],
            gateway=_DOWN,
        )
        asyncio.run(server._run_ib_script_with_recovery("ib_sync.py", []))
        skipped = asyncio.run(server._run_ib_script_with_recovery("ib_orders.py", []))
        assert skipped.error == server._IB_COOLDOWN_SKIP_ERROR
        assert [c[0] for c in calls] == ["ib_sync.py"]

    def test_cancel_is_never_refused_by_the_cooldown(self, monkeypatch):
        calls = _wire(monkeypatch, results=[], gateway=_DOWN)
        monkeypatch.setattr(server, "_ib_last_failure", server.time.monotonic())
        result = asyncio.run(server._run_ib_script_with_recovery(
            "ib_order_manage.py", ["cancel", "--perm-id", "42"],
        ))
        assert result.ok is True
        assert calls == [("ib_order_manage.py", ["cancel", "--perm-id", "42"])]

    def test_modify_still_waits_out_an_armed_cooldown(self, monkeypatch):
        calls = _wire(monkeypatch, results=[], gateway=_DOWN)
        monkeypatch.setattr(server, "_ib_last_failure", server.time.monotonic())
        result = asyncio.run(server._run_ib_script_with_recovery(
            "ib_order_manage.py", ["modify", "--perm-id", "42", "--new-price", "1.0"],
        ))
        assert result.error == server._IB_COOLDOWN_SKIP_ERROR
        assert calls == []


@pytest.mark.parametrize("mode", ["cloud", "managed"])
def test_indeterminate_text_survives_recovery_rewrite_as_504(monkeypatch, mode):
    calls = _wire(
        monkeypatch,
        results=[ScriptResult(
            ok=False,
            error="Script exited with code 1: confirmation request timed out",
        )],
        gateway=_DOWN,
    )
    monkeypatch.setattr(server, "is_cloud_mode", lambda: mode == "cloud")
    monkeypatch.setattr(server, "record_order_event", AsyncMock())
    body = {"type": "stock", "symbol": "AAPL", "action": "BUY", "quantity": 1,
            "limitPrice": 1.0, "orderRef": "radon-test-ref"}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(server._orders_place_after_rate_reservation(body))

    assert len(calls) == 1
    assert exc.value.status_code == 504
    assert exc.value.detail["code"] == "ORDER_INDETERMINATE"
    assert exc.value.detail["orderRef"] == "radon-test-ref"


def test_pre_transmit_connection_failure_stays_502(monkeypatch):
    _wire(
        monkeypatch,
        results=[ScriptResult(ok=False, error="API connection failed: ConnectionRefusedError")],
        gateway=_DOWN,
    )
    monkeypatch.setattr(server, "record_order_event", AsyncMock())
    body = {"type": "stock", "symbol": "AAPL", "action": "BUY", "quantity": 1,
            "limitPrice": 1.0, "orderRef": "radon-test-ref"}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(server._orders_place_after_rate_reservation(body))
    assert exc.value.status_code == 502


class TestOneClassifier:
    @pytest.mark.parametrize("text", [
        "Connect call failed", "ECONNREFUSED", "connection refused on 127.0.0.1:4001",
        "API connection failed: TimeoutError()", "IBConnectionError",
        "Make sure API port on TWS/IBG is open",
        "Connectivity between IBKR and Trader Workstation has been lost",
        "account updates request timed out", "Failed to connect to IB",
    ])
    def test_raw_connection_text(self, text):
        assert ib_gateway.classify_ib_failure(text) is ib_gateway.IBFailure.CONNECTION
        assert server._is_ib_connection_error(text)

    @pytest.mark.parametrize("text", [
        server._IB_COOLDOWN_SKIP_ERROR,
        "IB Gateway is not responding (cloud mode). Check remote host and Tailscale.",
        "IB Gateway is not accepting connections. Check IBKR Mobile for 2FA approval.",
        "IB Gateway is down and restart failed. Check IBKR Mobile for 2FA approval.",
    ])
    def test_own_gateway_down_verdicts(self, text):
        assert ib_gateway.classify_ib_failure(text) is ib_gateway.IBFailure.GATEWAY_DOWN
        assert server._orders_sync_failure_is_gateway_down(text)

    @pytest.mark.parametrize("text", ["No security definition found", "", None])
    def test_other_text(self, text):
        assert ib_gateway.classify_ib_failure(text) is ib_gateway.IBFailure.NONE

    def test_service_health_heal_uses_the_same_classifier(self):
        blob = '{"message": "Connectivity between IBKR and Trader Workstation has been lost"}'
        assert ib_gateway._error_message_looks_like_ib_outage(blob)
