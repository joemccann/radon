"""Browser isolation for the security loops.

The wrappers unset PW_TEST_CONNECT_WS_ENDPOINT and set
RADON_WEEKEND_BROWSER_HOST=unavailable:disabled before every agent launch, so
no agent could drive a browser host the operator happened to have running. On
the runner those are the loop env's AGENT_UNSET and AGENT_ENV, which
run_loop.sh applies before the resolver, the hooks and the agent
(test_runner_run_loop.py drives that).
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _value(loop: str, key: str) -> str:
    text = (REPO / "scripts" / "runner" / "loops" / f"{loop}.env").read_text()
    return next(line for line in text.splitlines() if line.startswith(f"{key}=")).split("=", 1)[1].strip('"')


@pytest.mark.parametrize("loop", ["security", "security-deepsec"])
def test_the_agent_never_inherits_a_browser_host(loop):
    assert "PW_TEST_CONNECT_WS_ENDPOINT" in _value(loop, "AGENT_UNSET").split()
    assert "RADON_WEEKEND_BROWSER_HOST=unavailable:disabled" in _value(loop, "AGENT_ENV").split()
