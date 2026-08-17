"""REL-023 — the order path must always find a subprocess slot
(RELIABILITY_AUDIT.md R-048).

`MAX_CONCURRENT_SUBPROCESSES` is shared by every `run_script*` caller and the
slot is held for the subprocess's whole lifetime. With `LEAP_PRESET_TIMEOUT_S`
and `GARCH_PRESET_TIMEOUT_S` at 3600s, four routine scans can pin every slot
for an hour — and `POST /trading/kill` / `POST /orders/cancel-all` both run
`ib_cancel_all.py` through the same gate. The halt flag would still set (it is
a file write) so new placements stop, but the working orders the kill switch
exists to pull would stay live.

Fault injection: saturate the pool with long-running stub scripts, then prove
the order-lane scripts still admit while a routine scan is refused.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from api import subprocess as subprocess_mod  # noqa: E402

SERVER_PATH = SCRIPTS_DIR / "api" / "server.py"

_SLOW_STUB = "import sys, time\ntime.sleep(30)\n"
_FAST_STUB = 'import json\nprint(json.dumps({"status": "ok"}))\n'


@pytest.fixture
def stub_scripts(tmp_path, monkeypatch):
    """Point the runner at throwaway stubs so no real IB script is spawned."""
    (tmp_path / "slow_scan.py").write_text(_SLOW_STUB)
    (tmp_path / "leap_scanner_uw.py").write_text(_SLOW_STUB)
    (tmp_path / "ib_cancel_all.py").write_text(_FAST_STUB)
    (tmp_path / "ib_place_order.py").write_text(_FAST_STUB)
    monkeypatch.setattr(subprocess_mod, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(subprocess_mod, "_active_subprocesses", 0)
    return tmp_path


SATURATION_TIMEOUT_S = 20.0


async def _await_slots_held(count: int, *, timeout: float = SATURATION_TIMEOUT_S):
    """Block until `count` slots are actually held, or fail the test.

    Every admission assertion in this file is only meaningful once the general
    lane is FULL. A bare timed loop that falls through on a cold or contended
    runner (spawning CPython interpreters can easily outlast a couple of
    seconds) leaves spare capacity behind, and then "the order lane is admitted
    while scans saturate the pool" passes for the wrong reason — the
    reservation could be entirely broken and this suite would stay green.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while subprocess_mod._active_subprocesses < count:
        assert asyncio.get_running_loop().time() < deadline, (
            f"general lane never saturated: {subprocess_mod._active_subprocesses} of "
            f"{count} slots held after {timeout}s — the admission assertions that "
            "follow would pass on spare capacity, not on the reservation"
        )
        await asyncio.sleep(0.01)


async def _saturate_general_lane(count: int):
    """Launch `count` long-running scan subprocesses and wait until they hold
    their slots. Returns the tasks so the caller can cancel them."""
    tasks = [
        asyncio.create_task(subprocess_mod.run_script("slow_scan.py", [], timeout=30))
        for _ in range(count)
    ]
    await _await_slots_held(count)
    return tasks


async def _drain(tasks):
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


class TestOrderLaneReservation:
    def test_kill_switch_admits_while_scans_saturate_the_pool(
        self, stub_scripts, monkeypatch
    ):
        """The R-048 scenario: every general slot pinned by hour-long scans,
        then the operator fires the kill switch."""
        monkeypatch.setattr(subprocess_mod, "MAX_CONCURRENT_SUBPROCESSES", 2)
        monkeypatch.setattr(
            subprocess_mod, "RESERVED_ORDER_SLOTS", 1, raising=False
        )

        async def run():
            tasks = await _saturate_general_lane(1)
            try:
                refused = await subprocess_mod.run_script("slow_scan.py", [], timeout=5)
                cancel_all = await subprocess_mod.run_script(
                    "ib_cancel_all.py", [], timeout=10
                )
                return refused, cancel_all
            finally:
                await _drain(tasks)

        refused, cancel_all = asyncio.run(run())

        assert refused.ok is False
        assert "capacity exhausted" in (refused.error or "").lower()
        assert cancel_all.ok is True, cancel_all.error
        assert cancel_all.data == {"status": "ok"}

    def test_placement_admits_while_scans_saturate_the_pool(
        self, stub_scripts, monkeypatch
    ):
        monkeypatch.setattr(subprocess_mod, "MAX_CONCURRENT_SUBPROCESSES", 3)
        monkeypatch.setattr(
            subprocess_mod, "RESERVED_ORDER_SLOTS", 1, raising=False
        )

        async def run():
            tasks = await _saturate_general_lane(2)
            try:
                refused = await subprocess_mod.run_script(
                    "leap_scanner_uw.py", [], timeout=5
                )
                placed = await subprocess_mod.run_script(
                    "ib_place_order.py", ["--json", "{}"], timeout=10
                )
                return refused, placed
            finally:
                await _drain(tasks)

        refused, placed = asyncio.run(run())

        assert refused.ok is False
        assert "capacity exhausted" in (refused.error or "").lower()
        assert placed.ok is True, placed.error

    def test_order_lane_is_still_bounded_by_the_hard_cap(
        self, stub_scripts, monkeypatch
    ):
        """The reservation is a floor for the order path, not an exemption —
        the global cap still bounds fd/clientId/memory growth."""
        monkeypatch.setattr(subprocess_mod, "MAX_CONCURRENT_SUBPROCESSES", 2)
        monkeypatch.setattr(
            subprocess_mod, "RESERVED_ORDER_SLOTS", 1, raising=False
        )
        (stub_scripts / "ib_order_manage.py").write_text(_SLOW_STUB)

        async def run():
            tasks = [
                asyncio.create_task(
                    subprocess_mod.run_script("ib_order_manage.py", [], timeout=30)
                )
                for _ in range(2)
            ]
            await _await_slots_held(2)
            try:
                return await subprocess_mod.run_script(
                    "ib_cancel_all.py", [], timeout=5
                )
            finally:
                await _drain(tasks)

        over_cap = asyncio.run(run())
        assert over_cap.ok is False
        assert "capacity exhausted" in (over_cap.error or "").lower()

    def test_general_lane_keeps_at_least_one_slot(self, monkeypatch):
        """A misconfigured reservation must never starve the scan lane to zero."""
        monkeypatch.setattr(subprocess_mod, "MAX_CONCURRENT_SUBPROCESSES", 1)
        monkeypatch.setattr(
            subprocess_mod, "RESERVED_ORDER_SLOTS", 4, raising=False
        )
        assert subprocess_mod._general_lane_capacity() >= 1


class TestOrderLaneRegistry:
    def test_every_money_path_script_is_registered(self):
        """Any order-placing/cancelling script the API spawns must be in the
        lane, or a scan storm can lock it out again."""
        assert {
            "ib_place_order.py",
            "ib_order_manage.py",
            "ib_cancel_all.py",
        } <= subprocess_mod._ORDER_LANE_SCRIPTS

    def test_server_spawns_no_unregistered_order_script(self):
        """Contract sweep over the real call sites — mirrors the
        `_NON_IDEMPOTENT_IB_SCRIPTS` discipline."""
        import re

        source = SERVER_PATH.read_text()
        spawned = set(
            re.findall(r'"(ib_(?:place_order|order_manage|cancel_all|execute)\.py)"', source)
        )
        assert spawned, "expected the order scripts to be referenced in server.py"
        assert spawned <= subprocess_mod._ORDER_LANE_SCRIPTS


class _BlockingProcess:
    """Stand-in child that holds its slot until it is explicitly killed.

    Mirrors `test_ib_gateway_subprocess_cleanup._BlockingProcess`.
    """

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.killed = False
        self.waited = False
        self.returncode = None

    async def communicate(self):
        self.started.set()
        await self.release.wait()
        self.returncode = -9 if self.killed else 0
        return b"", b""

    def kill(self) -> None:
        self.killed = True
        self.release.set()

    async def wait(self) -> int:
        self.waited = True
        self.returncode = -9 if self.killed else 0
        return self.returncode


def _process_factory(proc):
    async def create(*args, **kwargs):
        return proc

    return create


class TestSlotAccountingUnderCancellation:
    """A cancelled request must not leave the child running with its slot freed.

    `_active_subprocesses` is what bounds fds, IB client ids and the reserved
    order lane. If a runner releases the slot on cancellation without killing
    the child, the counter under-counts live processes: the cap stops bounding
    anything, and an order slot reported as free can already be occupied by an
    orphan. For `run_module` the orphan is typically
    `trade_blotter.flex_query`, which keeps spending Flex requests against a
    token already under a 24h-to-168h throttle embargo.
    """

    @pytest.mark.parametrize(
        "runner,args",
        [
            ("run_script", ("slow_scan.py", [])),
            ("run_script_raw", ("slow_scan.py", [])),
            ("run_module", ("trade_blotter.flex_query", [])),
        ],
    )
    def test_cancellation_kills_the_child_and_frees_the_slot(
        self, stub_scripts, monkeypatch, runner, args
    ):
        proc = _BlockingProcess()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _process_factory(proc))
        monkeypatch.setattr(subprocess_mod, "_active_subprocesses", 0)

        async def run():
            task = asyncio.create_task(
                getattr(subprocess_mod, runner)(*args, timeout=30)
            )
            await asyncio.wait_for(proc.started.wait(), timeout=5)
            assert subprocess_mod._active_subprocesses == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(run())

        assert proc.killed is True, f"{runner} orphaned the child on cancellation"
        assert subprocess_mod._active_subprocesses == 0, (
            f"{runner} leaked a slot on cancellation"
        )
