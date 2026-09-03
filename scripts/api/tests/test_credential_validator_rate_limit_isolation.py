"""Regression coverage for process-wide credential validator test state."""
from __future__ import annotations

import sys
import time


def _route_module_instances():
    from scripts.api.routes import credentials

    modules = [credentials]
    doppelganger = sys.modules.get("api.routes.credentials")
    if doppelganger is not None and doppelganger is not credentials:
        modules.append(doppelganger)
    return modules


def test_a_validator_budget_can_be_spent() -> None:
    for module in _route_module_instances():
        module._validator_last_run["unusual_whales"] = time.monotonic()
        assert module._validator_last_run


def test_b_next_test_starts_with_a_fresh_validator_budget() -> None:
    for module in _route_module_instances():
        assert module._validator_last_run == {}
        assert module._validator_slots is None
