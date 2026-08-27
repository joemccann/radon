"""Admission gating and shed honesty.

R-216: the capacity-shed branch wrote a fabricated HEALTHY `service_health`
row — `status: "ok"`, `error: None`, `finished_at: now` — for a tick on which
`ib_orders.py --sync` never ran and `open_orders`/`executed_orders` were not
touched. No consecutive-shed counter, no ceiling, no distinct status. The
general lane is MAX_CONCURRENT(4) - RESERVED(1) = 3 and `ib_orders.py` is not
on the reserved lane, so a sustained saturation makes every 5-minute tick shed
forever: a silent watchdog, `orders-sync` green with a fresh timestamp, and an
orders table that has not moved. A fill or a cancel inside that window is
invisible.

R-217: `SCAN_GATES["gex"]` is ONE instance shared by every caller-supplied
`ticker`. (a) A ticker `gex_scan.py` cannot resolve raises the 502 mismatch,
`_gated_scan` catches it and calls `gate.mark_failure()` — arming a 60 s
backoff on the SHARED gate, so repeating a bogus request holds the real SPX
panel dead indefinitely. (b) `gex.json` holds exactly one ticker's payload, so
a successful NDX scan arms the cooldown but `read_cached()` returns None for an
SPX poll, `_admit()` falls through, and a second 120 s `run_script` is spawned
back to back. Two tickers polled alternately defeat the cooldown entirely.

R-259: `_admit()` checks `in_backoff()` BEFORE the cache, so one failure turns
a good cache written seconds earlier into a hard 429; and it tests `if hit:`
rather than `if hit is not None:`, so an empty-object cache is falsy and every
poll spawns another 120 s subprocess while the cooldown says otherwise.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from api import server as srv
from api.scan_gate import ScanGate
from api.subprocess import ScriptResult


class TestShedIsVisible:
    @pytest.mark.asyncio
    async def test_a_shed_does_not_heartbeat_ok(self):
        captured: list = []

        async def fake_execute(sql, args):
            captured.append(args)

        with patch.object(srv.db_http, "hrana_execute", side_effect=lambda *a: captured.append(a)):
            srv._reset_orders_sync_shed_state()
            await srv._heartbeat_orders_sync_skip("subprocess capacity exhausted")

        assert captured, "no health row was written at all"
        args = captured[0][1]
        assert args[1] == "warn", (
            f"a tick that never ran reported state {args[1]!r}"
        )
        assert args[1] != "ok"

    @pytest.mark.asyncio
    async def test_consecutive_sheds_escalate(self):
        states: list[str] = []

        def capture(sql, args):
            states.append(args[1])

        with patch.object(srv.db_http, "hrana_execute", side_effect=capture):
            srv._reset_orders_sync_shed_state()
            for _ in range(srv.ORDERS_SYNC_MAX_CONSECUTIVE_SHEDS + 1):
                await srv._heartbeat_orders_sync_skip("subprocess capacity exhausted")

        assert states[-1] == "error", (
            f"a permanently shedding loop never escalates: {states}"
        )

    @pytest.mark.asyncio
    async def test_a_successful_sync_clears_the_shed_streak(self):
        states: list[str] = []

        def capture(sql, args):
            states.append(args[1])

        with patch.object(srv.db_http, "hrana_execute", side_effect=capture):
            srv._reset_orders_sync_shed_state()
            for _ in range(srv.ORDERS_SYNC_MAX_CONSECUTIVE_SHEDS + 1):
                await srv._heartbeat_orders_sync_skip("capacity")
            srv._reset_orders_sync_shed_state()
            await srv._heartbeat_orders_sync_skip("capacity")

        assert states[-1] != "error"


class TestGexGateIsPerTicker:
    def test_each_ticker_gets_its_own_gate(self):
        spx = srv._scan_gate_for("gex", "SPX")
        ndx = srv._scan_gate_for("gex", "NDX")
        assert spx is not ndx
        assert spx is srv._scan_gate_for("gex", "SPX")

    def test_an_unresolvable_ticker_does_not_black_out_spx(self):
        srv._reset_scan_gates()
        bogus = srv._scan_gate_for("gex", "ZZZZ")
        bogus.mark_failure()
        assert bogus.in_backoff() is True
        assert srv._scan_gate_for("gex", "SPX").in_backoff() is False, (
            "a caller-supplied ticker armed a shared 60s backoff"
        )

    def test_alternating_tickers_do_not_defeat_the_cooldown(self):
        srv._reset_scan_gates()
        srv._scan_gate_for("gex", "NDX").mark_success()
        assert srv._scan_gate_for("gex", "NDX").in_cooldown() is True
        # SPX has its own gate and its own cache read, so it is admitted on
        # its own merits rather than riding NDX's cooldown.
        assert srv._scan_gate_for("gex", "SPX").in_cooldown() is False


class TestAdmitOrdering:
    def _gate(self, clock):
        return ScanGate("gex", cooldown_s=120, failure_backoff_s=60, clock=clock)

    @pytest.mark.asyncio
    async def test_a_good_cache_is_served_during_a_failure_backoff(self):
        now = [1000.0]
        gate = self._gate(lambda: now[0])
        gate.mark_success()
        gate.mark_failure()

        served = await srv._gated_scan(
            gate,
            lambda: {"scan_time": "2026-08-26T15:00:00Z"},
            AsyncMock(side_effect=AssertionError("must not run a scan")),
        )
        assert served["scan_time"] == "2026-08-26T15:00:00Z", (
            "a transient failure turned a good cache into a hard 429"
        )

    @pytest.mark.asyncio
    async def test_backoff_still_refuses_when_there_is_no_cache(self):
        from fastapi import HTTPException

        now = [1000.0]
        gate = self._gate(lambda: now[0])
        gate.mark_failure()
        with pytest.raises(HTTPException) as exc:
            await srv._gated_scan(
                gate, lambda: None,
                AsyncMock(side_effect=AssertionError("must not run a scan")),
            )
        assert exc.value.status_code == 429

    @pytest.mark.asyncio
    async def test_an_empty_cache_object_is_a_hit_not_a_miss(self):
        now = [1000.0]
        gate = self._gate(lambda: now[0])
        gate.mark_success()

        ran = []

        async def run():
            ran.append(1)
            return ScriptResult(ok=True, data={}, error=None)

        served = await srv._gated_scan(gate, lambda: {}, run)
        assert served == {}
        assert ran == [], (
            "an empty-object cache was falsy, so the cooldown was bypassed and "
            "another 120s subprocess was spawned on every poll"
        )
