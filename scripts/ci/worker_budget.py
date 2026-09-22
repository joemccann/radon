"""Worker budget for a local `pytest -n`.

xdist resolves `auto` to every physical core, and agents routinely pass
`-n 8` / `-n 12` by hand. On a developer laptop each worker holds the whole
imported app (~470 MB) and the suite runs beside an editor, browsers and
usually a sibling session's suite, so the local budget is half the cores:
`auto` resolves to it and an explicit count is clamped to it. CI and an
explicit `PYTEST_XDIST_AUTO_NUM_WORKERS` lift the budget (`None`). Wired by
the root conftest.
"""
from __future__ import annotations

from collections.abc import Mapping


def local_worker_budget(cpu_count: int) -> int:
    return max(1, cpu_count // 2)


def budgeted_workers(requested: int | None, cpu_count: int, env: Mapping[str, str]) -> int | None:
    """Workers to run: `requested` is an explicit `-n N`, None means `auto`."""
    if env.get("CI") or env.get("PYTEST_XDIST_AUTO_NUM_WORKERS"):
        return None
    budget = local_worker_budget(cpu_count)
    if requested is None:
        return budget
    return min(requested, budget)
