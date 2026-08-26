#!/usr/bin/env python3
"""A live 2FA lease must not be revoked on one unretried probe.

R-210: `_is_orphaned` returned True as soon as the lease was older than
GATEWAY_DOWN_GRACE_SECS and one 350 ms TCP connect failed — and
`_gateway_port_listening` swallows EVERY OSError: refused, ETIMEDOUT under host
load, EMFILE, or a misconfigured IB_GATEWAY_HOST in one of the several
processes that import this module. The grace is measured from `acquired_at`,
never from how long the port has been OBSERVED down, and no successful prior
probe is remembered, so a `docker compose restart`, an image pull or a slow IBC
startup that keeps the API port unbound for >90 s made a legitimately held
lease look orphaned. `acquire_2fa_push_lock` then OVERWRITES the original
holder's record, and the original holder's `release_2fa_push_lock` becomes a
no-op because `existing.holder != expected_holder` — leaving the thief's lease
for its full 600 s TTL. That is exactly the failure this module exists to
prevent: two stacked IBKR Mobile pushes and a lock file naming a component that
never restarted anything.

R-211: `remaining_lock_secs` and `consume_2fa_push_lock` never forwarded a
guard timeout, so `_guard` took the blocking-flock branch. The guard is held
across `_write_lock_file`'s two `os.fsync` calls, so on a stalled disk every
`/health` poll blocked forever inside the FastAPI handler with no timeout and
no log line. `ib_watchdog` already wraps both entry points in bounded helpers.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from utils import ib_2fa_lock


@pytest.fixture(autouse=True)
def _redirect_lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "ib-2fa-push-lock.json"
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(path))
    ib_2fa_lock.reset_orphan_state()
    return path


def _past_grace(base: float = 1000.0) -> float:
    return base + ib_2fa_lock.GATEWAY_DOWN_GRACE_SECS + 1


class TestOrphanNeedsConfirmation:
    def test_one_failed_probe_does_not_revoke_a_live_lease(self, monkeypatch):
        probes = {"n": 0}

        def flaky_probe():
            probes["n"] += 1
            return False  # a single transient OSError from create_connection

        monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", flaky_probe)
        ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=600, now=1000.0)

        held = ib_2fa_lock.check_2fa_push_lock(now=_past_grace())
        assert held is not None, "one unretried probe revoked a live lease"
        assert held.holder == "restart-cli"

    def test_a_sustained_outage_still_revokes(self, monkeypatch):
        monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", lambda: False)
        ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=600, now=1000.0)

        now = _past_grace()
        # Confirmation requires the port to be OBSERVED down more than once.
        for _ in range(ib_2fa_lock.ORPHAN_CONFIRM_PROBES):
            result = ib_2fa_lock.check_2fa_push_lock(now=now)
            now += 1.0
        assert result is None, "a genuinely dead gateway must still free the lease"

    def test_a_prior_successful_probe_resets_the_confirmation(self, monkeypatch):
        state = {"up": True}
        monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", lambda: state["up"])
        ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=600, now=1000.0)

        now = _past_grace()
        assert ib_2fa_lock.check_2fa_push_lock(now=now) is not None
        state["up"] = False
        # One failure after a known-good observation is a blip, not an orphan.
        assert ib_2fa_lock.check_2fa_push_lock(now=now + 1) is not None

    def test_a_second_holder_cannot_steal_on_one_failed_probe(self, monkeypatch):
        monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", lambda: False)
        ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=600, now=1000.0)

        ok, current = ib_2fa_lock.acquire_2fa_push_lock(
            "ib-watchdog", ttl_secs=600, now=_past_grace()
        )
        assert ok is False, "the lease was stolen on a single unconfirmed probe"
        assert current is not None and current.holder == "restart-cli"

        # And the original holder can still release its own lease.
        ib_2fa_lock.release_2fa_push_lock(expected_holder="restart-cli")
        assert ib_2fa_lock.check_2fa_push_lock(now=_past_grace()) is None


class TestGuardIsBounded:
    @pytest.mark.parametrize("name", ["remaining_lock_secs", "consume_2fa_push_lock"])
    def test_entry_points_accept_a_guard_timeout(self, name):
        signature = inspect.signature(getattr(ib_2fa_lock, name))
        assert "guard_timeout_secs" in signature.parameters, (
            f"{name} cannot bound the flock it takes across two fsync calls"
        )

    def test_remaining_lock_secs_forwards_its_deadline(self, monkeypatch):
        seen: list = []
        real_check = ib_2fa_lock.check_2fa_push_lock

        def spy(now=None, *, guard_timeout_secs=None):
            seen.append(guard_timeout_secs)
            return real_check(now, guard_timeout_secs=guard_timeout_secs)

        monkeypatch.setattr(ib_2fa_lock, "check_2fa_push_lock", spy)
        ib_2fa_lock.remaining_lock_secs(now=1000.0, guard_timeout_secs=2.5)
        assert seen == [2.5], (
            "the deadline was dropped; _guard falls back to a blocking flock"
        )

    def test_a_wedged_guard_raises_rather_than_hanging(self, monkeypatch):
        def wedged(*args, **kwargs):
            raise ib_2fa_lock.GuardLockTimeout("guard busy")

        monkeypatch.setattr(ib_2fa_lock, "_guard", wedged)
        with pytest.raises(ib_2fa_lock.GuardLockTimeout):
            ib_2fa_lock.remaining_lock_secs(now=1000.0, guard_timeout_secs=0.01)
