"""Repo-root conftest: budget `pytest -n` on developer machines.

xdist's `auto` claims every physical core (14 on the operator's laptop) and
each worker imports the whole app (~470 MB), so one local run took ~6.5 GB
and a load average over 100 (2026-09-07). Half the cores locally, for `auto`
and for an explicit `-n N`; CI and `PYTEST_XDIST_AUTO_NUM_WORKERS` lift the
budget. Policy and tests: `scripts/ci/worker_budget.py`,
`test_local_xdist_worker_budget.py`. `optionalhook` keeps a pytest without
xdist installed from rejecting the xdist hook.
"""
from __future__ import annotations

import os

import pytest

from ci.worker_budget import budgeted_workers


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int | None:
    return budgeted_workers(None, os.cpu_count() or 1, os.environ)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Clamp an explicit `-n N` before xdist's session start spawns the nodes.

    xdist's cmdline_main has already expanded `-n N` into N `popen` specs on
    `config.option.tx`, so the budget is applied to that list; a hand-written
    `--tx` topology is left alone.
    """
    specs = list(getattr(config.option, "tx", None) or [])
    if not specs or any(spec != "popen" for spec in specs):
        return
    clamped = budgeted_workers(len(specs), os.cpu_count() or 1, os.environ)
    if clamped is None or clamped >= len(specs):
        return
    config.option.numprocesses = clamped
    config.option.tx = ["popen"] * clamped
