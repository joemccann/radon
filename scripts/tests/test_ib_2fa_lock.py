"""Unit tests for ``scripts/utils/ib_2fa_lock.py``.

Pins down the cross-process advisory lock used to prevent stacked IBKR
2FA pushes when multiple restart paths fire close together.

The lock is filesystem-backed (so the FastAPI process and the
``ib_watchdog`` oneshot both see the same state). Each test redirects
``IB_2FA_LOCK_PATH`` to a tmp file so production state is never touched.
"""

from __future__ import annotations

from pathlib import Path

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


def test_second_holder_is_rejected_while_lock_is_active():
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


def test_same_holder_reacquire_refreshes_expiry():
    """Two restart calls from the same caller must not deadlock each other —
    they're the same logical operation. The lock's lease is renewed instead
    of refusing the call."""
    ok1, lock1 = ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=60, now=1000.0)
    ok2, lock2 = ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=60, now=1030.0)
    assert ok1 is True and ok2 is True
    assert lock2 is not None
    assert lock2.acquired_at == 1030.0
    assert lock2.expires_at == 1090.0


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


def test_corrupt_lock_file_is_treated_as_free(_redirect_lock_path: Path):
    """A garbled JSON byte on disk must NOT wedge the system — better
    to allow a restart than to hang in perpetuity. The next acquire
    overwrites the corrupt content."""
    _redirect_lock_path.parent.mkdir(parents=True, exist_ok=True)
    _redirect_lock_path.write_text("{not valid json")
    assert ib_2fa_lock.check_2fa_push_lock(now=1000.0) is None
    ok, lock = ib_2fa_lock.acquire_2fa_push_lock("restart-cli", ttl_secs=60, now=1000.0)
    assert ok is True
    assert lock is not None


# --- remaining_lock_secs ----------------------------------------------------


def test_remaining_lock_secs_returns_zero_when_free():
    assert ib_2fa_lock.remaining_lock_secs(now=1000.0) == 0


def test_remaining_lock_secs_counts_down_toward_expiry():
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


def test_cli_acquire_same_holder_refreshes_lease():
    ib_2fa_lock.acquire_2fa_push_lock("radon-cli", ttl_secs=600)
    assert ib_2fa_lock.main(["acquire", "radon-cli"]) == 0


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
