"""Unit tests for ``scripts/utils/ib_2fa_lock.py``.

Pins down the cross-process advisory lock used to prevent stacked IBKR
2FA pushes when multiple restart paths fire close together.

The lock is filesystem-backed (so the FastAPI process and the
``ib_watchdog`` oneshot both see the same state). Each test redirects
``IB_2FA_LOCK_PATH`` to a tmp file so production state is never touched.
"""

from __future__ import annotations

from pathlib import Path
import threading

import pytest

from utils import ib_2fa_lock


@pytest.fixture(autouse=True)
def _redirect_lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own lock file so the production
    /var/lib/radon/ib-2fa-push-lock.json is never touched."""
    path = tmp_path / "ib-2fa-push-lock.json"
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(path))
    return path


# --- check + acquire baseline -----------------------------------------------


def test_check_returns_none_when_lock_file_absent():
    assert ib_2fa_lock.check_2fa_push_lock(now=1000.0) is None


def test_acquire_on_empty_state_succeeds():
    ok, lock = ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=60, now=1000.0)
    assert ok is True
    assert lock is not None
    assert lock.holder == "restart-cli"
    assert lock.acquired_at == 1000.0
    assert lock.expires_at == 1060.0


def test_check_returns_active_lock_after_acquire():
    ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=60, now=1000.0)
    held = ib_2fa_lock.check_2fa_push_lock(now=1010.0)
    assert held is not None
    assert held.holder == "restart-cli"


def test_check_returns_none_when_lock_has_expired():
    ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=60, now=1000.0)
    # 1 second past expiry — lock counts as free.
    assert ib_2fa_lock.check_2fa_push_lock(now=1061.0) is None


# --- rejection while held ---------------------------------------------------


def test_second_holder_is_rejected_while_lock_is_active(gateway_port_up):
    ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=600, now=1000.0)
    ok, current = ib_2fa_lock.acquire_2fa_push_lock("ib-watchdog", ttl_secs=600, now=1100.0)
    assert ok is False
    assert current is not None
    assert current.holder == "restart-cli"


def test_second_holder_can_acquire_after_lock_expires():
    ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=60, now=1000.0)
    ok, lock = ib_2fa_lock.acquire_2fa_push_lock("ib-watchdog", ttl_secs=60, now=1100.0)
    assert ok is True
    assert lock is not None
    assert lock.holder == "ib-watchdog"


def test_same_holder_reacquire_is_rejected_while_lease_is_active():
    """Holder names identify components, not individual restart operations.

    A second call from the same component is still a second 2FA push and must
    remain blocked until the first lease expires or is explicitly released.
    """
    ok1, lock1 = ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=60, now=1000.0)
    ok2, lock2 = ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=60, now=1030.0)
    assert ok1 is True and ok2 is False
    assert lock2 is not None
    assert lock2.acquired_at == 1000.0
    assert lock2.expires_at == 1060.0


def test_concurrent_acquire_serializes_read_modify_write(monkeypatch):
    """Two contenders that both reach the read boundary must not both win."""
    original_read = ib_2fa_lock._read_lock_file
    barrier = threading.Barrier(2)

    def synchronized_read():
        value = original_read()
        try:
            barrier.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass
        return value

    monkeypatch.setattr(ib_2fa_lock, "_read_lock_file", synchronized_read)
    results = []
    errors = []

    def acquire(holder):
        try:
            results.append(
                ib_2fa_lock.acquire_2fa_push_lock(holder, ttl_secs=60, now=1000.0)
            )
        except BaseException as exc:  # pragma: no cover - assertion reports detail
            errors.append(exc)

    threads = [threading.Thread(target=acquire, args=(holder,)) for holder in ("api", "watchdog")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert not errors
    assert len(results) == 2
    assert sum(1 for acquired, _lock in results if acquired) == 1


# --- release ---------------------------------------------------------------


def test_release_clears_active_lock():
    ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=600, now=1000.0)
    previous = ib_2fa_lock.release_2fa_push_lock()
    assert previous is not None
    assert previous.holder == "restart-cli"
    assert ib_2fa_lock.check_2fa_push_lock(now=1100.0) is None


def test_release_on_free_lock_is_idempotent_noop():
    assert ib_2fa_lock.release_2fa_push_lock() is None


def test_acquire_after_release_succeeds_for_new_holder():
    """The post-release path is the key escape hatch: operator hits
    /ib/reset-backoff (which releases the lock) and the next restart
    attempt — even from a different holder — must go through."""
    ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=600, now=1000.0)
    ib_2fa_lock.release_2fa_push_lock()
    ok, lock = ib_2fa_lock.acquire_2fa_push_lock("ib-watchdog", ttl_secs=600, now=1010.0)
    assert ok is True
    assert lock is not None
    assert lock.holder == "ib-watchdog"


# --- persistence across processes ------------------------------------------


def test_lock_persists_via_filesystem(_redirect_lock_path: Path):
    """The whole point of the design: the lock crosses process
    boundaries because it lives on disk. Simulate by reading the file
    directly and validating its content."""
    import json

    ib_2fa_lock.acquire_2fa_push_lock(
        "restart-cli", ttl_secs=600, reason="user restart", now=1000.0
    )
    with _redirect_lock_path.open() as fh:
        data = json.load(fh)
    assert data["holder"] == "restart-cli"
    assert data["acquired_at"] == 1000.0
    assert data["expires_at"] == 1600.0
    assert data["reason"] == "user restart"


def test_recent_corrupt_lock_file_fails_closed(_redirect_lock_path: Path):
    """A recently corrupted lease may represent a push already in flight."""
    _redirect_lock_path.parent.mkdir(parents=True, exist_ok=True)
    _redirect_lock_path.write_text("{not valid json")
    stat_now = _redirect_lock_path.stat().st_mtime
    held = ib_2fa_lock.check_2fa_push_lock(now=stat_now)
    assert held is not None
    assert held.holder == "unreadable-lock-state"
    ok, lock = ib_2fa_lock.acquire_2fa_push_lock(
        "restart-cli", ttl_secs=60, now=stat_now
    )
    assert ok is False
    assert lock is not None


# --- remaining_lock_secs ----------------------------------------------------


def test_remaining_lock_secs_returns_zero_when_free():
    assert ib_2fa_lock.remaining_lock_secs(now=1000.0) == 0


def test_remaining_lock_secs_counts_down_toward_expiry(gateway_port_up):
    ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=600, now=1000.0)
    assert ib_2fa_lock.remaining_lock_secs(now=1000.0) == 600
    assert ib_2fa_lock.remaining_lock_secs(now=1100.0) == 500
    # Past expiry → 0, never negative.
    assert ib_2fa_lock.remaining_lock_secs(now=2000.0) == 0


# --- CLI entry point ---------------------------------------------------------
#
# `python3 -m scripts.utils.ib_2fa_lock {check|acquire <holder>|release <holder>}`
# is consumed by shell control planes (radon-cloud/scripts/operator-radon.sh)
# that cannot import the module. Exit codes are the contract:
#   0 = free / acquired / released, 1 = held by another holder, 2 = usage error.


def test_cli_check_exits_zero_when_free(capsys):
    assert ib_2fa_lock.main(["check"]) == 0
    assert "free" in capsys.readouterr().out


def test_cli_check_exits_nonzero_and_names_holder_when_held(capsys):
    ib_2fa_lock.acquire_2fa_push_lock("ib-watchdog", ttl_secs=600)
    assert ib_2fa_lock.main(["check"]) == 1
    err = capsys.readouterr().err
    assert "ib-watchdog" in err
    assert "remaining" in err


def test_cli_acquire_succeeds_on_free_lock(capsys, _redirect_lock_path: Path):
    assert ib_2fa_lock.main(["acquire", "radon-cli"]) == 0
    assert "acquired" in capsys.readouterr().out
    held = ib_2fa_lock.check_2fa_push_lock()
    assert held is not None
    assert held.holder == "radon-cli"


def test_cli_acquire_refused_names_holder_and_remaining(capsys):
    ib_2fa_lock.acquire_2fa_push_lock("ib-watchdog", ttl_secs=600)
    assert ib_2fa_lock.main(["acquire", "radon-cli"]) == 1
    err = capsys.readouterr().err
    assert "ib-watchdog" in err
    assert "remaining" in err
    # Lock untouched — still owned by the original holder.
    held = ib_2fa_lock.check_2fa_push_lock()
    assert held is not None and held.holder == "ib-watchdog"


def test_cli_acquire_same_holder_is_refused_while_lease_active():
    ib_2fa_lock.acquire_2fa_push_lock("radon-cli", ttl_secs=600)
    assert ib_2fa_lock.main(["acquire", "radon-cli"]) == 1


def test_cli_release_own_lock(capsys):
    ib_2fa_lock.acquire_2fa_push_lock("radon-cli", ttl_secs=600)
    assert ib_2fa_lock.main(["release", "radon-cli"]) == 0
    assert "released" in capsys.readouterr().out
    assert ib_2fa_lock.check_2fa_push_lock() is None


def test_cli_release_refuses_lock_held_by_another_holder(capsys):
    ib_2fa_lock.acquire_2fa_push_lock("ib-watchdog", ttl_secs=600)
    assert ib_2fa_lock.main(["release", "radon-cli"]) == 1
    assert "ib-watchdog" in capsys.readouterr().err
    held = ib_2fa_lock.check_2fa_push_lock()
    assert held is not None and held.holder == "ib-watchdog"


def test_cli_release_when_free_is_noop():
    assert ib_2fa_lock.main(["release", "radon-cli"]) == 0


@pytest.mark.parametrize(
    "argv",
    [[], ["bogus"], ["acquire"], ["release"], ["check", "extra"]],
)
def test_cli_usage_errors_exit_two(argv):
    assert ib_2fa_lock.main(argv) == 2


def test_cli_resolves_via_python_dash_m(tmp_path: Path):
    """The shell control planes invoke `python3 -m scripts.utils.ib_2fa_lock`
    from the repo root — pin that module path + exit-code contract end to end."""
    import subprocess
    import sys as _sys

    repo_root = Path(__file__).resolve().parents[2]
    env = dict(**__import__("os").environ)
    env["IB_2FA_LOCK_PATH"] = str(tmp_path / "cli-lock.json")

    acquire = subprocess.run(
        [_sys.executable, "-m", "scripts.utils.ib_2fa_lock", "acquire", "radon-cli"],
        cwd=repo_root, env=env, capture_output=True, text=True,
    )
    assert acquire.returncode == 0
    assert "acquired" in acquire.stdout

    refused = subprocess.run(
        [_sys.executable, "-m", "scripts.utils.ib_2fa_lock", "acquire", "ib-watchdog"],
        cwd=repo_root, env=env, capture_output=True, text=True,
    )
    assert refused.returncode == 1
    assert "radon-cli" in refused.stderr


# --- orphaned lease: Gateway provably down ----------------------------------
#
# 2026-08-25 incident. An operator restart took the lease at 12:40:46, an admin
# stop of radon-ib-gateway.service tore the container down 14s later, and the
# stop path left the lease held. With no container there was no push in flight,
# yet every recovery path (Start Gateway, Restart All Services, watchdog) stayed
# blocked for the rest of the 600s TTL.


@pytest.fixture
def gateway_port_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", lambda: False)


@pytest.fixture
def gateway_port_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ib_2fa_lock, "_gateway_port_listening", lambda: True)


def _past_grace(base: float = 1000.0) -> float:
    return base + ib_2fa_lock.GATEWAY_DOWN_GRACE_SECS + 1


def test_check_drops_lease_once_gateway_port_is_down_past_grace(gateway_port_down):
    ib_2fa_lock.acquire_2fa_push_lock("radon-cloud.ib-gateway-control", now=1000.0)
    assert ib_2fa_lock.check_2fa_push_lock(now=_past_grace()) is None


def test_acquire_succeeds_over_a_lease_orphaned_by_a_downed_gateway(gateway_port_down):
    ib_2fa_lock.acquire_2fa_push_lock("radon-cloud.ib-gateway-control", now=1000.0)
    ok, lock = ib_2fa_lock.acquire_2fa_push_lock("ib-watchdog", now=_past_grace())
    assert ok is True
    assert lock is not None and lock.holder == "ib-watchdog"


def test_lease_survives_the_grace_window_while_the_container_boots(gateway_port_down):
    """The lease exists a beat before the container binds 4001 — do not eat it."""
    ib_2fa_lock.acquire_2fa_push_lock("radon-cloud.ib-gateway-control", now=1000.0)
    held = ib_2fa_lock.check_2fa_push_lock(
        now=1000.0 + ib_2fa_lock.GATEWAY_DOWN_GRACE_SECS - 1
    )
    assert held is not None and held.holder == "radon-cloud.ib-gateway-control"


def test_lease_is_honoured_while_the_gateway_awaits_the_2fa_tap(gateway_port_up):
    """auth_state=awaiting_2fa keeps port 4001 listening — a real push in flight."""
    ib_2fa_lock.acquire_2fa_push_lock("radon-cloud.ib-gateway-control", now=1000.0)
    held = ib_2fa_lock.check_2fa_push_lock(now=_past_grace())
    assert held is not None and held.holder == "radon-cloud.ib-gateway-control"


def test_remaining_secs_reports_zero_for_an_orphaned_lease(gateway_port_down):
    ib_2fa_lock.acquire_2fa_push_lock("radon-cloud.ib-gateway-control", now=1000.0)
    assert ib_2fa_lock.remaining_lock_secs(now=_past_grace()) == 0


def test_port_probe_is_skipped_while_the_lease_is_young(monkeypatch):
    """Cheap by construction: /health must not open a socket on every poll."""
    probes: list[int] = []
    monkeypatch.setattr(
        ib_2fa_lock, "_gateway_port_listening", lambda: probes.append(1) or True
    )
    ib_2fa_lock.acquire_2fa_push_lock("radon-cloud.ib-gateway-control", now=1000.0)
    ib_2fa_lock.check_2fa_push_lock(now=1010.0)
    assert probes == []


def test_cli_release_any_clears_a_lease_held_by_another_component(capsys):
    ib_2fa_lock.acquire_2fa_push_lock("ib-watchdog", now=1000.0)
    assert ib_2fa_lock.main(["release-any"]) == 0
    assert "ib-watchdog" in capsys.readouterr().out
    assert ib_2fa_lock.check_2fa_push_lock(now=1010.0) is None


def test_cli_release_any_when_free_is_noop():
    assert ib_2fa_lock.main(["release-any"]) == 0


def test_orphan_warning_is_logged_once_per_lease(gateway_port_down, caplog, monkeypatch):
    """/health re-reads the lease every poll; the journal must not fill with it."""
    monkeypatch.setattr(ib_2fa_lock, "_orphan_reported", None)
    ib_2fa_lock.acquire_2fa_push_lock("radon-cloud.ib-gateway-control", now=1000.0)
    with caplog.at_level("WARNING", logger="radon.ib_2fa_lock"):
        for _ in range(5):
            ib_2fa_lock.check_2fa_push_lock(now=_past_grace())
    assert sum("ignored: Gateway port down" in r.message for r in caplog.records) == 1
