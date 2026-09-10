"""Interactive routes must not be starved by long-running scans.

2026-09-08: `GET /options/expirations?symbol=VIX` and
`GET /index-options/chain?symbol=VIX` 502'd "Subprocess capacity exhausted"
on app.radon.run while three general-lane slots were pinned by cri/vcg/gex
scans (120-180s budgets) plus the 30s-cadence ib_sync/ib_orders runs. The
4-slot host cap minus the reserved order slot left 3 slots that batch scans
could fill completely, and every user-facing chain request was refused the
instant it arrived.

Two contracts:

1. A caller whose timeout exceeds `LONG_RUNNING_TIMEOUT_S` is batch work and
   may not occupy the `RESERVED_INTERACTIVE_SLOTS` floor.
2. An interactive caller (short timeout) waits up to
   `SUBPROCESS_ADMISSION_WAIT_S` (never past its own timeout) for a slot to
   free before it is refused. Batch callers stay fail-fast: they have caches
   and the scan-gate backoff.
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

_SLOW_STUB = "import time\ntime.sleep(30)\n"
_BRIEF_STUB = 'import json, time\ntime.sleep(1)\nprint(json.dumps({"status": "ok"}))\n'
_FAST_STUB = 'import json\nprint(json.dumps({"status": "ok"}))\n'

SATURATION_TIMEOUT_S = 20.0


@pytest.fixture
def stub_scripts(tmp_path, monkeypatch):
    (tmp_path / "cri_scan.py").write_text(_SLOW_STUB)
    (tmp_path / "vcg_scan.py").write_text(_SLOW_STUB)
    (tmp_path / "ib_sync.py").write_text(_BRIEF_STUB)
    (tmp_path / "ib_option_chain.py").write_text(_FAST_STUB)
    monkeypatch.setattr(subprocess_mod, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(subprocess_mod, "_active_subprocesses", 0)
    return tmp_path


async def _await_slots_held(count: int, *, timeout: float = SATURATION_TIMEOUT_S):
    deadline = asyncio.get_running_loop().time() + timeout
    while subprocess_mod._active_subprocesses < count:
        assert asyncio.get_running_loop().time() < deadline, (
            f"lane never saturated: {subprocess_mod._active_subprocesses} of {count}"
        )
        await asyncio.sleep(0.01)


async def _drain(tasks):
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _configure(monkeypatch, *, hard_cap: int, order: int = 1, interactive: int = 1, wait_s: float):
    monkeypatch.setattr(subprocess_mod, "MAX_CONCURRENT_SUBPROCESSES", hard_cap)
    monkeypatch.setattr(subprocess_mod, "RESERVED_ORDER_SLOTS", order)
    monkeypatch.setattr(subprocess_mod, "RESERVED_INTERACTIVE_SLOTS", interactive)
    monkeypatch.setattr(subprocess_mod, "SUBPROCESS_ADMISSION_WAIT_S", wait_s)


class TestInteractiveReservation:
    def test_long_running_scans_cannot_take_the_interactive_slot(
        self, stub_scripts, monkeypatch
    ):
        """Hard cap 3, one order slot, one interactive slot: batch scans get
        ONE slot between them, and the chain call still admits."""
        _configure(monkeypatch, hard_cap=3, wait_s=0.0)

        async def run():
            scans = [
                asyncio.create_task(
                    subprocess_mod.run_script("cri_scan.py", ["--json"], timeout=180)
                )
            ]
            await _await_slots_held(1)
            try:
                second_scan = await subprocess_mod.run_script(
                    "vcg_scan.py", ["--json"], timeout=120
                )
                chain = await subprocess_mod.run_script(
                    "ib_option_chain.py", ["--symbol", "VIX"], timeout=45
                )
                return second_scan, chain
            finally:
                await _drain(scans)

        second_scan, chain = asyncio.run(run())

        assert second_scan.ok is False
        assert "capacity exhausted" in (second_scan.error or "").lower()
        assert chain.ok is True, chain.error
        assert chain.data == {"status": "ok"}

    def test_lane_capacities(self, monkeypatch):
        _configure(monkeypatch, hard_cap=4, wait_s=0.0)
        assert subprocess_mod._lane_capacity("ib_place_order.py", 30) == 4
        assert subprocess_mod._lane_capacity("ib_option_chain.py", 45) == 3
        assert subprocess_mod._lane_capacity("cri_scan.py", 180) == 2

    def test_long_running_lane_keeps_at_least_one_slot(self, monkeypatch):
        _configure(monkeypatch, hard_cap=2, order=1, interactive=4, wait_s=0.0)
        assert subprocess_mod._lane_capacity("cri_scan.py", 180) >= 1


class TestInteractiveAdmissionWait:
    def test_interactive_caller_waits_for_a_freed_slot(self, stub_scripts, monkeypatch):
        """One general slot, held ~1s by ib_sync; the chain request arriving
        meanwhile is admitted once it frees instead of 502'ing instantly."""
        _configure(monkeypatch, hard_cap=2, interactive=0, wait_s=5.0)

        async def run():
            holder = asyncio.create_task(
                subprocess_mod.run_script("ib_sync.py", ["--sync"], timeout=30)
            )
            await _await_slots_held(1)
            chain = await subprocess_mod.run_script(
                "ib_option_chain.py", ["--symbol", "VIX"], timeout=45
            )
            await holder
            return chain

        chain = asyncio.run(run())
        assert chain.ok is True, chain.error

    def test_interactive_caller_is_refused_after_the_wait(self, stub_scripts, monkeypatch):
        _configure(monkeypatch, hard_cap=2, interactive=0, wait_s=0.3)

        async def run():
            holder = asyncio.create_task(
                subprocess_mod.run_script("cri_scan.py", ["--json"], timeout=30)
            )
            await _await_slots_held(1)
            started = asyncio.get_running_loop().time()
            try:
                refused = await subprocess_mod.run_script(
                    "ib_option_chain.py", ["--symbol", "VIX"], timeout=45
                )
                return refused, asyncio.get_running_loop().time() - started
            finally:
                await _drain([holder])

        refused, waited = asyncio.run(run())
        assert refused.ok is False
        assert refused.error == "Subprocess capacity exhausted"
        assert 0.3 <= waited < 3.0

    def test_batch_callers_stay_fail_fast(self, stub_scripts, monkeypatch):
        """Scans have caches and the scan-gate backoff; a scan that waited
        here would hold its gate lock for nothing."""
        _configure(monkeypatch, hard_cap=2, interactive=0, wait_s=5.0)

        async def run():
            holder = asyncio.create_task(
                subprocess_mod.run_script("cri_scan.py", ["--json"], timeout=30)
            )
            await _await_slots_held(1)
            started = asyncio.get_running_loop().time()
            try:
                refused = await subprocess_mod.run_script(
                    "vcg_scan.py", ["--json"], timeout=120
                )
                return refused, asyncio.get_running_loop().time() - started
            finally:
                await _drain([holder])

        refused, waited = asyncio.run(run())
        assert refused.ok is False
        assert waited < 1.0

    def test_wait_never_exceeds_the_callers_own_timeout(self, monkeypatch):
        _configure(monkeypatch, hard_cap=4, wait_s=10.0)
        assert subprocess_mod._admission_wait_s("ib_option_chain.py", 45) == 10.0
        assert subprocess_mod._admission_wait_s("ib_option_chain.py", 2) == 2.0
        assert subprocess_mod._admission_wait_s("cri_scan.py", 180) == 0.0

    def test_raw_and_module_runners_share_the_admission_policy(
        self, stub_scripts, monkeypatch
    ):
        _configure(monkeypatch, hard_cap=2, interactive=0, wait_s=5.0)

        async def run():
            holder = asyncio.create_task(
                subprocess_mod.run_script("ib_sync.py", ["--sync"], timeout=30)
            )
            await _await_slots_held(1)
            raw = await subprocess_mod.run_script_raw(
                "ib_option_chain.py", ["--symbol", "VIX"], timeout=45
            )
            await holder
            return raw

        raw = asyncio.run(run())
        assert raw.ok is True, raw.stderr
