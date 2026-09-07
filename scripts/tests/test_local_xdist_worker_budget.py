"""Local `pytest -n` must not claim every core on a developer machine.

xdist's default `auto` is `psutil.cpu_count(logical=False)`: 14 on the
operator's M-series laptop, where each worker holds ~470 MB after importing
the app. A single `-n auto` run therefore took ~6.5 GB and a load average
over 100, and a concurrent vitest run pushed the box 8 GB into swap
(2026-09-07). Sibling agent sessions also pass `-n 8` / `-n 12` by hand, so
an explicit count is clamped to the same budget. CI is unchanged: GitHub
runners have 4 vCPUs and the budget defers to xdist's own default there.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ci.worker_budget import budgeted_workers, local_worker_budget

REPO = Path(__file__).resolve().parents[2]


def test_local_auto_uses_half_the_cores():
    assert budgeted_workers(None, 14, {}) == 7
    assert budgeted_workers(None, 4, {}) == 2


def test_local_auto_never_drops_below_one_worker():
    assert budgeted_workers(None, 1, {}) == 1


def test_explicit_count_above_the_budget_is_clamped():
    assert budgeted_workers(12, 14, {}) == 7


def test_explicit_count_within_the_budget_is_kept():
    assert budgeted_workers(4, 14, {}) == 4


def test_ci_lifts_the_budget():
    assert budgeted_workers(None, 14, {"CI": "true"}) is None
    assert budgeted_workers(12, 14, {"CI": "true"}) is None


def test_explicit_env_override_lifts_the_budget():
    env = {"PYTEST_XDIST_AUTO_NUM_WORKERS": "12"}
    assert budgeted_workers(None, 14, env) is None
    assert budgeted_workers(12, 14, env) is None


def _workers_spawned(monkeypatch, numprocesses: str) -> int:
    """The wire: run one fast test under `-n <numprocesses>` for real.

    xdist spawns no workers under --collect-only; the default verbosity
    prints the scheduling line `N workers [1 item]`.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-n", numprocesses, "-p", "no:cacheprovider",
         str(REPO / "scripts/tests/test_local_xdist_worker_budget.py"),
         "-k", "test_local_auto_uses_half_the_cores"],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    scheduled = re.search(r"^(\d+) workers \[1 item\]", proc.stdout, re.MULTILINE)
    assert scheduled, proc.stdout + proc.stderr
    return int(scheduled.group(1))


@pytest.mark.parametrize("numprocesses", ["auto", "64"])
def test_root_conftest_applies_the_budget_on_the_wire(monkeypatch, numprocesses):
    assert _workers_spawned(monkeypatch, numprocesses) == local_worker_budget(os.cpu_count() or 1)
