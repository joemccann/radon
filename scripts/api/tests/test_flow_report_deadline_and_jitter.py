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
