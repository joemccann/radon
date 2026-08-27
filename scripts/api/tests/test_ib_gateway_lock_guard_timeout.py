"""Every ib_2fa_lock call in the FastAPI gateway helper must be time-bounded.

R-211 bounded the lease guard because `_guard(exclusive=True)` is held across
`_write_lock_file`'s two `os.fsync` calls: on a stalled disk an unbounded
`flock` hangs inside the FastAPI handler with no timeout and no log line.
`ib_watchdog` wrapped all three entry points; `scripts/api/ib_gateway.py`
converted only the two READ paths (`check_2fa_push_lock`,
`remaining_lock_secs`) and left the WRITE paths — `acquire_2fa_push_lock` and
`release_2fa_push_lock`, the ones that actually hold the exclusive guard across
the fsyncs — unbounded. `POST /ib/restart` therefore blocked forever while any
other holder owned the guard.

Nothing here touches a real gateway, socket or container: the lock module is
patched and the lease file is redirected to tmp_path. T-200.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scripts.api import ib_gateway  # noqa: E402
from utils import ib_2fa_lock  # noqa: E402


@pytest.fixture(autouse=True)
def _redirect_lock_path(tmp_path, monkeypatch):
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))


@pytest.fixture(autouse=True)
def _reset_backoff_state():
    ib_gateway._restart_state["attempt_count"] = 0
    ib_gateway._restart_state["next_attempt_after"] = 0.0
    ib_gateway._restart_state["last_attempt_at"] = 0.0
    ib_gateway._restart_state["last_outcome"] = None
    yield
    ib_gateway._restart_state["attempt_count"] = 0
    ib_gateway._restart_state["next_attempt_after"] = 0.0


def _boundable_lock_functions() -> set[str]:
    """Names in ib_2fa_lock that accept a guard deadline, read from the source
    of truth rather than a hand-maintained list."""
    names = set()
    for name, obj in vars(ib_2fa_lock).items():
        if name.startswith("_") or not inspect.isfunction(obj):
            continue
        if "guard_timeout_secs" in inspect.signature(obj).parameters:
            names.add(name)
    return names


def _unbounded_lock_call_sites(module) -> list[str]:
    source = Path(inspect.getsourcefile(module)).read_text()
    tree = ast.parse(source)
    boundable = _boundable_lock_functions()
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "ib_2fa_lock":
            continue
        if func.attr not in boundable:
            continue
        if any(kw.arg == "guard_timeout_secs" for kw in node.keywords):
            continue
        offenders.append(f"line {node.lineno}: ib_2fa_lock.{func.attr}(...)")
    return offenders


class TestEveryCallSiteIsBounded:
    def test_the_boundable_surface_is_not_empty(self):
        assert _boundable_lock_functions() >= {
            "check_2fa_push_lock",
            "acquire_2fa_push_lock",
            "release_2fa_push_lock",
            "remaining_lock_secs",
        }

    def test_ib_gateway_never_calls_the_lease_unbounded(self):
        offenders = _unbounded_lock_call_sites(ib_gateway)
        assert offenders == [], (
            "scripts/api/ib_gateway.py calls ib_2fa_lock without "
            "guard_timeout_secs; the guard is held across _write_lock_file's "
            "two fsyncs, so these hang the FastAPI handler:\n  "
            + "\n  ".join(offenders)
        )

    def test_ib_watchdog_never_calls_the_lease_unbounded(self):
        """The reference implementation this module has to match."""
        import ib_watchdog  # type: ignore[import-not-found]

        assert _unbounded_lock_call_sites(ib_watchdog) == []


class TestReadPathTimeout:
    def test_backoff_state_reports_guard_timeout_as_a_held_lease(self, monkeypatch):
        """A read that cannot get the guard must FAIL CLOSED: report a held
        lease so the caller defers instead of firing a second blind push."""

        def _timeout(*args, **kwargs):
            raise ib_2fa_lock.GuardLockTimeout("2FA lease guard busy after 5s")

        monkeypatch.setattr(ib_2fa_lock, "check_2fa_push_lock", _timeout)

        push_lock = ib_gateway.restart_backoff_state()["push_lock"]

        assert push_lock is not None
        assert push_lock["holder"] == "guard-timeout"
        assert push_lock["remaining_secs"] > 0

    def test_remaining_secs_reports_unknown_on_guard_timeout(self, monkeypatch):
        def _timeout(*args, **kwargs):
            raise ib_2fa_lock.GuardLockTimeout("2FA lease guard busy after 5s")

        monkeypatch.setattr(ib_2fa_lock, "remaining_lock_secs", _timeout)

        assert ib_gateway._remaining_lock_secs_bounded(1000.0) == -1


class TestWritePathTimeout:
    """The gap R-211 left: the exclusive guard spans both fsyncs."""

    @staticmethod
    def _acquire_times_out(monkeypatch):
        def _timeout(*args, **kwargs):
            raise ib_2fa_lock.GuardLockTimeout("2FA lease guard busy after 5s")

        monkeypatch.setattr(ib_2fa_lock, "acquire_2fa_push_lock", _timeout)

    def test_ensure_lease_defers_instead_of_raising(self, monkeypatch):
        self._acquire_times_out(monkeypatch)

        refusal = ib_gateway._acquire_gateway_push_lease(
            ib_gateway.IB_GATEWAY_ENSURE_LOCK_HOLDER, reason="startup ensure"
        )

        assert refusal is not None, "a guard timeout must never read as acquired"
        assert refusal["restarted"] is False
        assert refusal["deferred"] is True
        assert refusal["reason"] == "2fa_push_in_flight"
        assert refusal["lock_holder"] == "guard-timeout"

    def test_restart_defers_and_never_cycles_the_gateway(self, monkeypatch):
        self._acquire_times_out(monkeypatch)

        async def fail_restart_docker():
            raise AssertionError(
                "restart_ib_gateway must NOT cycle the Gateway when the lease "
                "guard timed out — it cannot know whether a push is in flight"
            )

        monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: False)
        monkeypatch.setattr(ib_gateway, "is_docker_mode", lambda: True)
        monkeypatch.setattr(ib_gateway, "_restart_docker", fail_restart_docker)

        result = asyncio.run(ib_gateway.restart_ib_gateway())

        assert result["restarted"] is False
        assert result.get("deferred") is True
        assert result.get("reason") == "2fa_push_in_flight"
        assert result.get("lock_holder") == "guard-timeout"

    def test_reset_backoff_survives_a_release_guard_timeout(self, monkeypatch):
        """The operator escape hatch must still clear the in-memory backoff
        even when the lease file's guard is wedged."""

        def _timeout(*args, **kwargs):
            raise ib_2fa_lock.GuardLockTimeout("2FA lease guard busy after 5s")

        monkeypatch.setattr(ib_2fa_lock, "release_2fa_push_lock", _timeout)
        ib_gateway._restart_state["attempt_count"] = 4
        ib_gateway._restart_state["next_attempt_after"] = 9_999_999.0

        result = ib_gateway.reset_restart_backoff()

        assert result["reset"] is True
        assert result["lock_released"] is None
        assert ib_gateway._restart_state["attempt_count"] == 0
        assert ib_gateway._restart_state["next_attempt_after"] == 0.0
