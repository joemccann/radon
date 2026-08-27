#!/usr/bin/env python3
"""The orphan-confirmation memory in ``ib_2fa_lock`` must not leak between tests.

R-210 added two module-level mutable globals — ``_orphan_reported`` and
``_orphan_seen_up`` — plus a real ``time.sleep`` between confirmation probes.
Both are process-wide. Only ``test_ib_2fa_lock.py`` and
``test_ib_2fa_lock_orphan_confirmation.py`` reset them; the other importers
(the watchdog suites, the api restart suites) do not, so within one xdist
worker a revocation outcome depended on which file ran first and every
unguarded revocation paid the real inter-probe sleep.

These tests pin the isolation contract itself: whatever a sibling test did to
the module globals, the next test starts from a clean slate. T-226.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from utils import ib_2fa_lock


_HOLDER = "leaky-holder"
_ACQUIRED_AT = 1000.0


@pytest.fixture
def lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test lease file. Deliberately does NOT reset the module globals —
    that is the shared conftest fixture's job and this file is the probe."""
    path = tmp_path / "ib-2fa-push-lock.json"
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(path))
    return path


def _past_grace() -> float:
    return _ACQUIRED_AT + ib_2fa_lock.GATEWAY_DOWN_GRACE_SECS + 1


def _hold_a_past_grace_lease() -> None:
    ib_2fa_lock.acquire_2fa_push_lock(
        _HOLDER, ttl_secs=600, reason="isolation probe", now=_ACQUIRED_AT
    )


def test_a_revocation_records_the_lease_in_module_state(lock_path, monkeypatch):
    """The production path that writes ``_orphan_reported``: a past-grace lease
    whose gateway port is down for every confirmation probe."""
    monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", lambda: False)
    _hold_a_past_grace_lease()

    assert ib_2fa_lock.check_2fa_push_lock(now=_past_grace()) is None
    assert ib_2fa_lock._orphan_reported == (_HOLDER, _ACQUIRED_AT)


def test_b_a_live_probe_records_the_lease_as_seen_up(lock_path, monkeypatch):
    """The production path that writes ``_orphan_seen_up``: a past-grace lease
    whose gateway answers, i.e. a restart rather than an abandoned holder."""
    assert ib_2fa_lock._orphan_seen_up is None, (
        "leaked _orphan_seen_up from a sibling test"
    )
    monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", lambda: True)
    _hold_a_past_grace_lease()

    held = ib_2fa_lock.check_2fa_push_lock(now=_past_grace())
    assert held is not None and held.holder == _HOLDER
    assert ib_2fa_lock._orphan_seen_up == (_HOLDER, _ACQUIRED_AT)


def test_c_next_test_starts_from_clean_orphan_state(lock_path):
    """Neither global survives into the next test in the same worker."""
    assert ib_2fa_lock._orphan_reported is None, (
        "leaked _orphan_reported from a sibling test"
    )
    assert ib_2fa_lock._orphan_seen_up is None, (
        "leaked _orphan_seen_up from a sibling test"
    )


def test_d_inter_probe_sleep_is_neutralised_for_the_suite(lock_path):
    """An unguarded revocation costs ORPHAN_CONFIRM_PROBES-1 real sleeps.

    Seven importing test files never zeroed the interval, so each revocation
    they drove burned it for real. The shared fixture must neutralise it for
    every test that imports this module, not just the two that opted in.
    """
    assert ib_2fa_lock.ORPHAN_CONFIRM_INTERVAL_SECS == 0.0
