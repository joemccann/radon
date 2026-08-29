"""R-354 / R-355 / R-356 / REL-128: the shed retry is bounded and desynchronised.

R-354: `_run_script_retrying_capacity` was bounded by attempt count with NO
total wall-clock deadline. `POST /flow-analysis/{ticker}` passes
`timeout=300, retries=2, delay_s=8`, so worst-case single-request wall clock
was `3 x 300 + 16 = 916s` with the FastAPI worker held throughout — against a
proxy that gives up at 130s and then takes the stale-cache branch. The retries
kept a general-lane slot occupied for a response nobody would read.

R-355: the delay was a fixed constant applied verbatim, so every client shed
in the same instant retried in the same instant — synchronised waves against a
lane already saturated — and orders-sync shared the same 8.0 value, landing
both chains on one grid.

R-356: an exhausted shed was indistinguishable from a first shed, so the
client copy told the operator to refresh for a proven-persistent condition.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR.parent))

import server  # noqa: E402

SHED = f"{server._CAPACITY_SHED_MARKER} for flow_report.py"


async def _noop_sleep(_secs):
    """`server.asyncio` IS the global asyncio module, so a lambda delegating to
    `asyncio.sleep` would recurse into the patch."""
    return None


def _shed_result():
    return server.ScriptResult(ok=False, data=None, error=SHED)


class TestTotalDeadline:
    def test_the_chain_gives_up_once_the_deadline_is_spent(self, monkeypatch):
        slept: list[float] = []
        calls: list[float] = []
        # The clock is advanced BY the fake attempt, not by call count:
        # `asyncio.run` itself reads time.monotonic during loop setup, so a
        # call-counting stub hands the wrong value to `started`.
        clock = {"t": 0.0}

        async def _run(_script, _args, *, timeout):
            calls.append(timeout)
            clock["t"] += 200.0
            return _shed_result()

        async def _sleep(secs):
            slept.append(secs)

        monkeypatch.setattr(server, "run_script", _run)
        monkeypatch.setattr(server.asyncio, "sleep", _sleep)
        monkeypatch.setattr(server.time, "monotonic", lambda: clock["t"])

        result = asyncio.run(
            server._run_script_retrying_capacity(
                "flow_report.py", ["AAPL"],
                timeout=300, retries=2, delay_s=5.0, label="t",
                deadline_s=120.0,
            )
        )

        assert result.ok is False
        assert slept == [], "the chain must not sleep past its own deadline"
        assert len(calls) == 1

    def test_each_attempt_timeout_is_clamped_to_the_remaining_budget(self, monkeypatch):
        calls: list[float] = []

        async def _run(_script, _args, *, timeout):
            calls.append(timeout)
            return _shed_result()

        monkeypatch.setattr(server, "run_script", _run)
        monkeypatch.setattr(server.asyncio, "sleep", _noop_sleep)
        ticks = iter([0.0] * 20)
        monkeypatch.setattr(server.time, "monotonic", lambda: next(ticks, 0.0))

        asyncio.run(
            server._run_script_retrying_capacity(
                "flow_report.py", ["AAPL"],
                timeout=300, retries=2, delay_s=1.0, label="t",
                deadline_s=120.0,
            )
        )
        assert calls, "at least one attempt must run"
        assert all(t <= 120.0 for t in calls), (
            f"a 300s attempt outlives the 120s budget and the 130s proxy; {calls}"
        )

    def test_no_deadline_keeps_the_original_unbounded_behaviour(self, monkeypatch):
        calls: list[float] = []

        async def _run(_script, _args, *, timeout):
            calls.append(timeout)
            return _shed_result()

        monkeypatch.setattr(server, "run_script", _run)
        monkeypatch.setattr(server.asyncio, "sleep", _noop_sleep)
        asyncio.run(
            server._run_script_retrying_capacity(
                "flow_report.py", ["AAPL"],
                timeout=300, retries=2, delay_s=0.0, label="t",
            )
        )
        assert calls == [300, 300, 300]


class TestBackoffHasJitter:
    def test_successive_delays_differ_and_grow(self, monkeypatch):
        slept: list[float] = []

        async def _run(_script, _args, *, timeout):
            return _shed_result()

        async def _sleep(secs):
            slept.append(secs)

        monkeypatch.setattr(server, "run_script", _run)
        monkeypatch.setattr(server.asyncio, "sleep", _sleep)
        asyncio.run(
            server._run_script_retrying_capacity(
                "flow_report.py", ["AAPL"],
                timeout=1, retries=2, delay_s=5.0, label="t",
            )
        )
        assert len(slept) == 2
        assert slept[0] != slept[1], "a fixed delay synchronises every shed client"
        # Exponential: the second window is strictly above the first's floor.
        assert slept[1] > 5.0 * 0.5

    def test_the_two_lanes_no_longer_share_one_delay_constant(self):
        assert (
            server.FLOW_REPORT_SHED_RETRY_DELAY_SECS
            != server.ORDERS_SYNC_SHED_RETRY_DELAY_SECS
        ), "identical constants put both retry chains on the same grid"


class TestExhaustedShedIsDistinct:
    def test_an_exhausted_shed_names_its_attempt_count(self, monkeypatch):
        async def _run(_script, _args, *, timeout):
            return _shed_result()

        monkeypatch.setattr(server, "run_script", _run)
        monkeypatch.setattr(server.asyncio, "sleep", _noop_sleep)
        result = asyncio.run(
            server._run_script_retrying_capacity(
                "flow_report.py", ["AAPL"],
                timeout=1, retries=2, delay_s=0.0, label="t",
            )
        )
        assert result.ok is False
        assert "attempts" in result.error
        assert server._CAPACITY_SHED_MARKER in result.error

    def test_a_first_shed_that_then_succeeds_is_not_relabelled(self, monkeypatch):
        outcomes = [_shed_result(), server.ScriptResult(ok=True, data={"x": 1}, error=None)]

        async def _run(_script, _args, *, timeout):
            return outcomes.pop(0)

        monkeypatch.setattr(server, "run_script", _run)
        monkeypatch.setattr(server.asyncio, "sleep", _noop_sleep)
        result = asyncio.run(
            server._run_script_retrying_capacity(
                "flow_report.py", ["AAPL"],
                timeout=1, retries=2, delay_s=0.0, label="t",
            )
        )
        assert result.ok is True


class TestTheShedBudgetIsActuallySpent:
    """2026-08-28: /flow-analysis/AMZN served a Jun 16 report.

    A capacity shed is fail-fast — `_claim_subprocess_slot` returns False with
    no awaits — so the whole retry chain cost ~21s of a 120s budget and 502'd
    at 18:24:21 while the journal shows general-lane slots freeing every few
    seconds (regime/gex/vcg scans completing at 18:24:30-35). The Next.js
    route then served the June cache. `retries` was the real bound, never the
    deadline it was paired with.
    """

    @staticmethod
    def _budget_clock(monkeypatch, calls, timeouts):
        clock = {"t": 0.0}

        async def _run(_script, _args, *, timeout):
            calls.append(clock["t"])
            timeouts.append(timeout)
            return _shed_result()

        async def _sleep(secs):
            clock["t"] += secs

        monkeypatch.setattr(server, "run_script", _run)
        monkeypatch.setattr(server.asyncio, "sleep", _sleep)
        monkeypatch.setattr(server.time, "monotonic", lambda: clock["t"])

    def test_a_shed_chain_probes_the_lane_for_most_of_its_budget(self, monkeypatch):
        calls: list[float] = []
        timeouts: list[float] = []
        self._budget_clock(monkeypatch, calls, timeouts)

        result = asyncio.run(
            server._run_script_retrying_capacity(
                "flow_report.py", ["AMZN"],
                timeout=300,
                retries=server.FLOW_REPORT_SHED_RETRIES,
                delay_s=server.FLOW_REPORT_SHED_RETRY_DELAY_SECS,
                label="t",
                deadline_s=server.FLOW_REPORT_TOTAL_DEADLINE_SECS,
                min_run_s=server.FLOW_REPORT_MIN_RUN_SECS,
            )
        )

        assert result.ok is False
        assert len(calls) >= 5, (
            "three fail-fast probes spend ~21s of the budget and hand the "
            f"operator a months-old cache; probed at {calls}"
        )

    def test_the_backoff_is_capped_so_late_probes_still_happen(self, monkeypatch):
        calls: list[float] = []
        timeouts: list[float] = []
        self._budget_clock(monkeypatch, calls, timeouts)

        asyncio.run(
            server._run_script_retrying_capacity(
                "flow_report.py", ["AMZN"],
                timeout=300,
                retries=server.FLOW_REPORT_SHED_RETRIES,
                delay_s=server.FLOW_REPORT_SHED_RETRY_DELAY_SECS,
                label="t",
                deadline_s=server.FLOW_REPORT_TOTAL_DEADLINE_SECS,
                min_run_s=server.FLOW_REPORT_MIN_RUN_SECS,
            )
        )

        gaps = [b - a for a, b in zip(calls, calls[1:])]
        assert gaps, "the chain must retry at least once"
        # Gaps are differences of an accumulated float clock, so a capped 20.0
        # reads back as 20.000000000000007. Compare with a tolerance, not ==.
        assert max(gaps) <= server._SHED_BACKOFF_CAP_SECS + 1e-6, (
            f"uncapped exponential backoff swallows the budget; gaps={gaps}"
        )

    def test_it_never_claims_a_slot_it_cannot_finish_the_scan_in(self, monkeypatch):
        calls: list[float] = []
        timeouts: list[float] = []
        self._budget_clock(monkeypatch, calls, timeouts)

        asyncio.run(
            server._run_script_retrying_capacity(
                "flow_report.py", ["AMZN"],
                timeout=300,
                retries=server.FLOW_REPORT_SHED_RETRIES,
                delay_s=server.FLOW_REPORT_SHED_RETRY_DELAY_SECS,
                label="t",
                deadline_s=server.FLOW_REPORT_TOTAL_DEADLINE_SECS,
                min_run_s=server.FLOW_REPORT_MIN_RUN_SECS,
            )
        )

        assert timeouts, "at least one attempt must run"
        assert all(t >= server.FLOW_REPORT_MIN_RUN_SECS for t in timeouts), (
            "a slot claimed with less budget than a scan needs burns the lane "
            f"and the UW spend on a run that must time out; timeouts={timeouts}"
        )


class TestTheBudgetFitsARealScan:
    """A 20-session AMZN pull measured 81s. A budget that cannot seat one real
    scan plus a retry window can only ever serve cache."""

    def test_the_deadline_seats_a_real_scan_and_a_retry_window(self):
        assert server.FLOW_REPORT_TOTAL_DEADLINE_SECS >= (
            server.FLOW_REPORT_MIN_RUN_SECS + 60.0
        ), "no room to probe for a slot before the scan itself has to start"

    def test_the_route_answers_before_the_edge_cuts_it(self):
        """Caddy 502s the app upstream that has not written a response header
        within `response_header_timeout`. A route that waits longer than that
        can only ever hand the operator a raw edge 502 in place of Radon's own
        answer — and the scan it was waiting for now lands on its own."""
        repo = Path(__file__).resolve().parents[3]
        route = (
            repo / "web" / "app" / "api" / "flow-analysis" / "[ticker]" / "route.ts"
        )
        match = re.search(r"timeout:\s*([\d_]+)", route.read_text())
        assert match, "the route must declare an explicit radonFetch timeout"
        route_secs = int(match.group(1).replace("_", "")) / 1000

        caddyfile = (repo / "cloud" / "caddy" / "Caddyfile").read_text()
        app_block = caddyfile.split("reverse_proxy localhost:3000")[1]
        edge = re.search(r"response_header_timeout\s+(\d+)s", app_block)
        assert edge, "the app upstream must state its response_header_timeout"

        assert route_secs < int(edge.group(1)), (
            f"a {route_secs}s route wait outlives the {edge.group(1)}s edge bound, "
            "so the operator gets a raw 502 instead of the dated cache"
        )
