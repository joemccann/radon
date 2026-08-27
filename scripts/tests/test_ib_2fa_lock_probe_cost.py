#!/usr/bin/env python3
"""The orphan-confirmation probes are expensive enough to matter to callers.

R-210 put ORPHAN_CONFIRM_PROBES socket connects plus the sleeps between them
inside `_is_orphaned`, which `check_2fa_push_lock` reaches on exactly the
incident state: a lease held past its grace window while the Gateway port is
down. Both existing suites zeroed ORPHAN_CONFIRM_INTERVAL_SECS in an autouse
fixture, so the real cost was invisible and nothing stopped `/health` from
calling it inline on the FastAPI event loop.

The clock is faked end to end: no socket is opened, no wall-clock second is
spent, and no gateway is touched. T-201.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from utils import ib_2fa_lock


_HOLDER = "scripts.api.ib_gateway.restart_ib_gateway"
_ACQUIRED_AT = 1000.0


@pytest.fixture
def lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "ib-2fa-push-lock.json"
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(path))
    return path


def test_check_on_a_down_port_blocks_for_every_confirmation_probe(
    lock_path, real_orphan_confirm_interval, monkeypatch
):
    """One `check_2fa_push_lock` costs N connect budgets plus N-1 sleeps.

    The worst case the budget is sized for is an unreachable IB_GATEWAY_HOST,
    where each connect burns its full `_PORT_PROBE_TIMEOUT_SECS` instead of
    being refused instantly.
    """
    blocked = {"secs": 0.0}
    probes = {"n": 0}

    def unreachable_host(address, timeout=None):
        probes["n"] += 1
        blocked["secs"] += float(timeout or 0.0)
        raise OSError("host unreachable")

    def fake_sleep(secs):
        blocked["secs"] += secs

    monkeypatch.setattr(ib_2fa_lock.socket, "create_connection", unreachable_host)
    monkeypatch.setattr(ib_2fa_lock.time, "sleep", fake_sleep)

    ib_2fa_lock.acquire_2fa_push_lock(
        _HOLDER, ttl_secs=600, reason="restart_ib_gateway", now=_ACQUIRED_AT
    )
    past_grace = _ACQUIRED_AT + ib_2fa_lock.GATEWAY_DOWN_GRACE_SECS + 1

    assert ib_2fa_lock.check_2fa_push_lock(now=past_grace) is None

    assert probes["n"] == ib_2fa_lock.ORPHAN_CONFIRM_PROBES
    expected = (
        ib_2fa_lock.ORPHAN_CONFIRM_PROBES * ib_2fa_lock._PORT_PROBE_TIMEOUT_SECS
        + (ib_2fa_lock.ORPHAN_CONFIRM_PROBES - 1) * real_orphan_confirm_interval
    )
    assert blocked["secs"] == pytest.approx(expected)
    # Guard the shape of the claim, not just the arithmetic: this is far too
    # long to run inline on an event loop that answers /health every 2s.
    assert blocked["secs"] > 1.0
