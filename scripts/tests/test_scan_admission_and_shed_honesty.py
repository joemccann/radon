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

    @pytest.mark.asyncio
    async def test_a_pool_disconnected_miss_is_error_and_ends_the_shed_streak(self):
        """T-460: the non-shed branch (`pool-disconnected`, server.py R-658).

        A stuck pool with the gateway reachable is a miss, not a shed: the row
        must read state='error' naming the miss, and the shed streak must
        restart so sheds separated by the unrelated failure do not inherit it
        and escalate the NEXT shed straight to error."""
        captured: list = []

        with patch.object(srv.db_http, "hrana_execute", side_effect=lambda *a: captured.append(a)):
            srv._reset_orders_sync_shed_state()
            for _ in range(srv.ORDERS_SYNC_MAX_CONSECUTIVE_SHEDS):
                await srv._heartbeat_orders_sync_skip("subprocess capacity exhausted")
            await srv._heartbeat_orders_sync_skip(
                "pool disconnected while gateway reachable",
                error_class="pool-disconnected",
            )
            await srv._heartbeat_orders_sync_skip("subprocess capacity exhausted")

        miss_args = captured[-2][1]
        assert miss_args[1] == "error", f"non-shed miss wrote state {miss_args[1]!r}"
        assert (
            "orders sync missed (pool-disconnected): "
            "pool disconnected while gateway reachable"
        ) in (miss_args[4] or "")
        assert captured[-1][1][1] == "warn", (
            "the shed after the miss inherited the stale streak"
        )


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


class TestSubjectGateMapIsBounded:
    """T-230: `_SUBJECT_SCAN_GATES` is keyed on a caller-supplied ticker.

    `/gex/scan` admits any <=10-char alnum symbol, so every novel value minted
    a gate in a dict with no cap and no eviction, in a process that lives for
    days. Two costs, not one: the map grows for the lifetime of the process,
    AND each novel key hands back a COLD gate, so a caller cycling symbols
    never meets a cooldown and spawns a fresh 120 s `run_script` every time.

    Eviction has to stay fail-CLOSED. Dropping a gate that is currently in
    cooldown or backoff is worse than keeping it: the next poll for that
    subject re-mints it cold and bypasses the exact backoff that was evicted,
    which is the R-217 storm re-entering through the cache. So only IDLE gates
    are evictable (LRU), and when every gate is armed a novel subject is
    REFUSED rather than admitted.
    """

    @staticmethod
    def _subjects(n: int, prefix: str = "T") -> list[str]:
        return [f"{prefix}{i:05d}" for i in range(n)]

    def test_five_hundred_distinct_tickers_do_not_grow_the_map_without_bound(self):
        srv._reset_scan_gates()
        for subject in self._subjects(500):
            srv._scan_gate_for("gex", subject)

        size = len(srv._SUBJECT_SCAN_GATES)
        assert size < 500, (
            f"500 caller-supplied tickers minted {size} gates: the map has no "
            "ceiling and grows for the life of the process"
        )
        assert size <= srv.MAX_SUBJECT_SCAN_GATES, (
            f"map holds {size} gates, ceiling is {srv.MAX_SUBJECT_SCAN_GATES}"
        )

    def test_eviction_is_lru_not_arbitrary(self):
        srv._reset_scan_gates()
        ceiling = srv.MAX_SUBJECT_SCAN_GATES
        subjects = self._subjects(ceiling)
        for subject in subjects:
            srv._scan_gate_for("gex", subject)
        assert len(srv._SUBJECT_SCAN_GATES) == ceiling

        # Touch the oldest key so it is now the most-recently-used, and keep
        # the identity so a silent re-mint cannot pass as a survivor.
        oldest, second_oldest = subjects[0], subjects[1]
        touched = srv._scan_gate_for("gex", oldest)

        srv._scan_gate_for("gex", "NOVELAAA")

        assert srv._SUBJECT_SCAN_GATES.get(("gex", oldest)) is touched, (
            "eviction dropped the most-recently-used gate"
        )
        assert ("gex", second_oldest) not in srv._SUBJECT_SCAN_GATES, (
            "eviction is not LRU: the least-recently-used idle gate survived"
        )
        assert len(srv._SUBJECT_SCAN_GATES) == ceiling

    def test_a_gate_in_cooldown_is_never_evicted(self):
        srv._reset_scan_gates()
        ceiling = srv.MAX_SUBJECT_SCAN_GATES

        # SPX is the FIRST key inserted, so it is the eviction candidate under
        # a naive LRU — and it is the one gate holding a live cooldown.
        spx = srv._scan_gate_for("gex", "SPX")
        spx.mark_success()
        assert spx.in_cooldown() is True

        for subject in self._subjects(ceiling * 2, prefix="C"):
            srv._scan_gate_for("gex", subject)

        assert srv._SUBJECT_SCAN_GATES.get(("gex", "SPX")) is spx, (
            "the gate holding a live cooldown was evicted; the next SPX poll "
            "re-mints it cold and spawns another 120s subprocess"
        )
        assert spx.in_cooldown() is True
        assert len(srv._SUBJECT_SCAN_GATES) <= ceiling

    def test_a_gate_in_backoff_is_never_evicted(self):
        srv._reset_scan_gates()
        ceiling = srv.MAX_SUBJECT_SCAN_GATES

        spx = srv._scan_gate_for("gex", "SPX")
        spx.mark_failure()
        assert spx.in_backoff() is True

        for subject in self._subjects(ceiling * 2, prefix="B"):
            srv._scan_gate_for("gex", subject)

        assert srv._SUBJECT_SCAN_GATES.get(("gex", "SPX")) is spx, (
            "the gate holding a live failure backoff was evicted; the next "
            "poll re-mints it cold and bypasses the backoff entirely"
        )
        assert spx.in_backoff() is True
        assert len(srv._SUBJECT_SCAN_GATES) <= ceiling

    def test_a_novel_subject_is_refused_when_every_gate_is_armed(self):
        srv._reset_scan_gates()
        ceiling = srv.MAX_SUBJECT_SCAN_GATES
        for subject in self._subjects(ceiling, prefix="A"):
            srv._scan_gate_for("gex", subject).mark_failure()
        assert len(srv._SUBJECT_SCAN_GATES) == ceiling

        overflow = srv._scan_gate_for("gex", "NOVELBBB")

        assert len(srv._SUBJECT_SCAN_GATES) == ceiling, (
            "a novel subject grew the map past the ceiling because every "
            "existing gate was armed and none could be evicted"
        )
        assert overflow.in_backoff() is True, (
            "with the whole map armed, a novel subject was handed a COLD gate "
            "and admitted — exactly the storm the gates exist to stop"
        )

    @pytest.mark.asyncio
    async def test_the_overflow_subject_gets_429_not_a_subprocess(self):
        srv._reset_scan_gates()
        for subject in self._subjects(srv.MAX_SUBJECT_SCAN_GATES, prefix="O"):
            srv._scan_gate_for("gex", subject).mark_failure()

        ran: list[int] = []

        async def run():
            ran.append(1)
            return ScriptResult(ok=True, data={"scan_time": "x"}, error=None)

        with pytest.raises(srv.HTTPException) as exc:
            await srv._gated_scan(
                srv._scan_gate_for("gex", "NOVELCCC"), lambda: None, run
            )

        assert exc.value.status_code == 429
        assert ran == [], "an overflowing novel subject still spawned a subprocess"

    def test_an_evicted_idle_gate_does_not_leak_across_scans(self):
        """The key is (scan, subject); the ceiling must not merge namespaces."""
        srv._reset_scan_gates()
        gex = srv._scan_gate_for("gex", "SPX")
        vcg = srv._scan_gate_for("vcg", "SPX")
        assert gex is not vcg
