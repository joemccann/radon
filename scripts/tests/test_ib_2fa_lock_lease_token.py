#!/usr/bin/env python3
"""The 2FA lease is one explicit machine, bound to its acquisition.

Audit 2026-11-03 §1 #19:

* Release matched the component name only. After an orphan revoke and a
  re-acquire by the same component, the first holder's late release deleted
  the second, live lease (ABA). Release and consume now take the lease's
  acquisition token (``acquired_at``) and refuse any other lease.
* ``consume`` skipped the orphan rule ``check`` and ``acquire`` honour, so a
  preheld restart could fire behind a lease any other path may already revoke.
* The orphan probe (up to three connects plus two sleeps) ran while the
  exclusive guard was held, stalling every other lease reader and writer.
* The broker's ``/status`` lease view ignored the orphan rule, so the app
  disarmed actions the helper would accept. Every reader now shares
  ``classify_lease``.
"""

from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from ib_gateway_remote import serve
from utils import ib_2fa_lock

HOLDER = "scripts.ib_watchdog.trigger_restart"
T0 = 1000.0


@pytest.fixture(autouse=True)
def _lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "ib-2fa-push-lock.json"
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(path))
    return path


@pytest.fixture
def port_down(monkeypatch):
    monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", lambda: False)


@pytest.fixture
def port_up(monkeypatch):
    monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", lambda: True)


def _past_grace(base: float = T0) -> float:
    return base + ib_2fa_lock.GATEWAY_DOWN_GRACE_SECS + 1


class TestClassification:
    def test_states(self, port_down):
        assert ib_2fa_lock.classify_lease(None, T0) is ib_2fa_lock.LeaseState.FREE
        ok, lock = ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600, now=T0)
        assert ok
        assert ib_2fa_lock.classify_lease(lock, T0 + 1) is ib_2fa_lock.LeaseState.HELD
        assert ib_2fa_lock.classify_lease(lock, T0 + 600) is ib_2fa_lock.LeaseState.EXPIRED
        assert ib_2fa_lock.classify_lease(lock, _past_grace()) is ib_2fa_lock.LeaseState.ORPHANED

    def test_unprobed_classification_never_calls_a_lease_orphaned(self, monkeypatch):
        def boom():
            raise AssertionError("probe must not run")

        monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", boom)
        lock = ib_2fa_lock.PushLock(HOLDER, T0, T0 + 600)
        state = ib_2fa_lock.classify_lease(lock, _past_grace(), probe=False)
        assert state is ib_2fa_lock.LeaseState.HELD


class TestReleaseIsBoundToTheAcquisition:
    def test_late_release_of_a_revoked_lease_keeps_the_new_one(self, port_down):
        ok, first = ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600, now=T0)
        assert ok
        # Gateway port down past grace: the same component re-acquires over
        # the orphan. The first run's release must not delete this lease.
        later = _past_grace()
        ok, second = ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600, now=later)
        assert ok and second.acquired_at == later

        released = ib_2fa_lock.release_2fa_push_lock(
            expected_holder=HOLDER, expected_acquired_at=first.acquired_at,
        )
        assert released is None
        with ib_2fa_lock._guard(exclusive=False):
            assert ib_2fa_lock._read_lock_file() == second

    def test_release_with_the_matching_token_clears(self, port_up):
        ok, lock = ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600, now=T0)
        released = ib_2fa_lock.release_2fa_push_lock(
            expected_holder=HOLDER, expected_acquired_at=lock.acquired_at,
        )
        assert released == lock
        with ib_2fa_lock._guard(exclusive=False):
            assert ib_2fa_lock._read_lock_file() is None

    def test_cli_release_with_token_refuses_another_acquisition(self, port_up, capsys):
        ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600)
        rc = ib_2fa_lock.main(["release", HOLDER, "12.5"])
        assert rc == 1
        assert ib_2fa_lock.check_2fa_push_lock() is not None

    def test_cli_acquire_prints_the_token(self, port_up, capsys):
        assert ib_2fa_lock.main(["acquire", HOLDER]) == 0
        out = capsys.readouterr().out
        lock = ib_2fa_lock.check_2fa_push_lock()
        assert f"acquired_at={lock.acquired_at!r}" in out
        assert ib_2fa_lock.main(["release", HOLDER, repr(lock.acquired_at)]) == 0
        assert ib_2fa_lock.check_2fa_push_lock() is None


class TestConsume:
    def test_consume_refuses_an_orphaned_lease(self, tmp_path, port_down):
        ok, _ = ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600, now=T0)
        assert ok
        consumed, current = ib_2fa_lock.consume_2fa_push_lock(
            HOLDER, tmp_path / "consumed.json", now=_past_grace(),
        )
        assert consumed is False
        assert not (tmp_path / "consumed.json").exists()

    def test_consume_is_bound_to_the_token(self, tmp_path, port_up):
        ok, lock = ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600, now=T0)
        consumed, _ = ib_2fa_lock.consume_2fa_push_lock(
            HOLDER, tmp_path / "consumed.json", now=T0 + 1, expected_acquired_at=T0 - 5,
        )
        assert consumed is False
        consumed, _ = ib_2fa_lock.consume_2fa_push_lock(
            HOLDER, tmp_path / "consumed.json", now=T0 + 1,
            expected_acquired_at=lock.acquired_at,
        )
        assert consumed is True

    def test_cli_consume_accepts_the_token(self, tmp_path, port_up):
        ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600)
        marker = str(tmp_path / "consumed.json")
        assert ib_2fa_lock.main(["consume", HOLDER, marker, "12.5"]) == 1
        lock = ib_2fa_lock.check_2fa_push_lock()
        assert ib_2fa_lock.main(["consume", HOLDER, marker, repr(lock.acquired_at)]) == 0


class TestProbeRunsOutsideTheGuard:
    def _probe_checking_guard(self, seen):
        guard_path = ib_2fa_lock._guard_path()

        def probe():
            with guard_path.open("a+") as fh:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    seen.append("guard-held")
                else:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    seen.append("guard-free")
            return False

        return probe

    def test_acquire_probes_without_holding_the_guard(self, monkeypatch):
        ib_2fa_lock.acquire_2fa_push_lock("other", ttl_secs=600, now=T0)
        seen: list[str] = []
        monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", self._probe_checking_guard(seen))
        ok, lock = ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600, now=_past_grace())
        assert ok and lock.holder == HOLDER
        assert seen and set(seen) == {"guard-free"}

    def test_consume_probes_without_holding_the_guard(self, tmp_path, monkeypatch):
        ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600, now=T0)
        seen: list[str] = []
        monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", self._probe_checking_guard(seen))
        consumed, _ = ib_2fa_lock.consume_2fa_push_lock(
            HOLDER, tmp_path / "consumed.json", now=_past_grace(),
        )
        assert consumed is False
        assert seen and set(seen) == {"guard-free"}

    def test_lease_replaced_during_the_probe_is_not_overwritten(self, monkeypatch):
        """The probe verdict is for the lease it probed. A lease written while
        the guard was down is a new acquisition and must be honoured."""
        ib_2fa_lock.acquire_2fa_push_lock("other", ttl_secs=600, now=T0)
        newer = ib_2fa_lock.PushLock("racer", _past_grace(), _past_grace() + 600)

        def probe():
            with ib_2fa_lock._guard(exclusive=True, timeout_secs=0.2):
                ib_2fa_lock._write_lock_file(newer)
            return False

        monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", probe)
        ok, current = ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600, now=_past_grace())
        assert ok is False
        assert current == newer


class TestBrokerStatusSharesTheRule:
    def test_orphaned_lease_is_not_reported_held(self, port_down):
        ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600, now=T0)
        monkey_now = _past_grace()
        assert serve.broker_lease(now=monkey_now) is None

    def test_live_lease_is_reported(self, port_up):
        ib_2fa_lock.acquire_2fa_push_lock(HOLDER, ttl_secs=600, now=T0)
        lease = serve.broker_lease(now=T0 + 5)
        assert lease is not None
        assert lease["holder"] == HOLDER
        assert lease["remaining_secs"] == 595
