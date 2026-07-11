"""``restart_ib_gateway`` must consult the shared 2FA push lock.

The bug this guards against (incident 2026-05-19): the user ran
``radon restart``, IB Gateway came up at ``awaiting_2fa``, the user
approved the push on their phone, IBKR reported "unsuccessful". Root
cause: a second restart fired ~3 minutes later from ``ib_watchdog.py``
hitting its api-hang threshold, sending a SECOND 2FA push while the
first was still pending. IBKR's backend cannot reconcile stacked
pushes — every approval gets rejected.

These tests pin down the new behavior layered on top of the existing
in-memory backoff:

  • Issuing a restart acquires the cross-process push lock so other
    paths (watchdog, operator CLI) see "in flight, don't push again".
  • A subsequent ``restart_ib_gateway`` call inside the lock window is
    REFUSED — even though the in-memory backoff might allow it (the two
    state machines work together; the lock is the hard ceiling).
  • A successful authenticated probe RELEASES the lock so the next
    legitimate restart can proceed immediately.
  • ``reset_restart_backoff`` (operator escape hatch) also releases the
    lock — "I just approved 2FA, try again now" must invalidate any
    in-flight lock from a previous failed cycle.
  • A timed-out lock is treated as released (the watchdog or any other
    consumer can proceed once IBKR's backend has had time to settle).
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
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
    """Every test gets a clean in-memory backoff so leakage from one
    test does not change the gating in another."""
    ib_gateway._restart_state["attempt_count"] = 0
    ib_gateway._restart_state["next_attempt_after"] = 0.0
    ib_gateway._restart_state["last_attempt_at"] = 0.0
    ib_gateway._restart_state["last_outcome"] = None
    ib_gateway._restart_state["last_accounts"] = []
    yield
    ib_gateway._restart_state["attempt_count"] = 0
    ib_gateway._restart_state["next_attempt_after"] = 0.0


def _patch_docker_mode(monkeypatch, restart_result, probe_result=(False, [])):
    """Force docker mode and stub the docker restart + probe.

    Default probe is "port up, no accounts" (= awaiting_2fa) so the
    first call exercises the failure path; tests that want the success
    path pass an explicit probe_result.
    """
    async def fake_restart_docker():
        return restart_result

    async def fake_probe(timeout=8.0):
        return probe_result

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(ib_gateway, "is_docker_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_restart_docker", fake_restart_docker)
    monkeypatch.setattr(ib_gateway, "_probe_authenticated", fake_probe)


# --- Successful restart releases the lock ----------------------------------


def test_authenticated_restart_releases_2fa_lock(monkeypatch):
    """A restart that lands at authenticated must NOT leave a stale
    lock — otherwise the next legitimate restart (hours later) would
    be blocked until the lock TTL expired."""
    _patch_docker_mode(
        monkeypatch,
        restart_result={"restarted": True, "port_listening": True, "gateway_mode": "docker"},
        probe_result=(True, ["U1234567"]),
    )

    result = asyncio.run(ib_gateway.restart_ib_gateway())

    assert result["authenticated"] is True
    assert ib_2fa_lock.check_2fa_push_lock() is None, (
        "successful authenticated restart must release the lock so the next "
        "restart (potentially hours later) is not blocked by a stale lock"
    )


# --- Failed restart holds the lock for follow-up callers -------------------


def test_awaiting_2fa_restart_holds_lock(monkeypatch):
    """A restart that lands at awaiting_2fa MUST acquire the lock so
    a second 2FA push from another restart path (ib_watchdog etc.) is
    refused while the user is still trying to approve the first push."""
    _patch_docker_mode(
        monkeypatch,
        restart_result={"restarted": True, "port_listening": True, "gateway_mode": "docker"},
        probe_result=(False, []),
    )

    asyncio.run(ib_gateway.restart_ib_gateway())

    held = ib_2fa_lock.check_2fa_push_lock()
    assert held is not None, "awaiting_2fa restart must hold the lock"
    assert "restart_ib_gateway" in held.holder


def test_lock_held_blocks_second_restart_even_without_backoff(monkeypatch):
    """The whole point of the lock: if another holder is mid-2FA-push,
    refuse the restart entirely — even if the in-memory backoff would
    otherwise allow it (e.g. fresh process, watchdog firing while
    FastAPI just restarted)."""
    # Simulate "another restart path holds the lock RIGHT NOW".
    ok, _ = ib_2fa_lock.acquire_2fa_push_lock(
        "ib-watchdog", ttl_secs=600, reason="api hang threshold"
    )
    assert ok is True

    async def fail_restart_docker():
        raise AssertionError(
            "restart_ib_gateway must NOT call _restart_docker while another "
            "holder owns the 2FA push lock"
        )

    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: False)
    monkeypatch.setattr(ib_gateway, "is_docker_mode", lambda: True)
    monkeypatch.setattr(ib_gateway, "_restart_docker", fail_restart_docker)

    result = asyncio.run(ib_gateway.restart_ib_gateway())

    assert result["restarted"] is False
    assert result.get("deferred") is True
    assert result.get("reason") == "2fa_push_in_flight"
    assert "ib-watchdog" in result["error"]


def test_lock_held_by_same_holder_blocks_second_restart(monkeypatch):
    """A holder name is not an operation id; reentry would mint a new push."""
    holder = "scripts.api.ib_gateway.restart_ib_gateway"
    ok, _ = ib_2fa_lock.acquire_2fa_push_lock(holder, ttl_secs=600)
    assert ok is True

    _patch_docker_mode(
        monkeypatch,
        restart_result={"restarted": True, "port_listening": True, "gateway_mode": "docker"},
        probe_result=(False, []),
    )

    result = asyncio.run(ib_gateway.restart_ib_gateway())
    assert result["restarted"] is False
    assert result["deferred"] is True
    assert result["reason"] == "2fa_push_in_flight"


def test_backoff_short_circuits_before_touching_push_lease(monkeypatch):
    """A deferred call must not acquire or refresh the cross-process lease."""
    # Force the local restart path. Importing scripts.api.server first can load
    # .env.ib-mode with IB_GATEWAY_MODE=cloud, which returns before backoff.
    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: False)
    ib_gateway._restart_state["attempt_count"] = 1
    ib_gateway._restart_state["next_attempt_after"] = time.time() + 120

    def fail_acquire(*args, **kwargs):
        raise AssertionError("backoff must be evaluated before lease acquisition")

    monkeypatch.setattr(ib_gateway.ib_2fa_lock, "acquire_2fa_push_lock", fail_acquire)

    result = asyncio.run(ib_gateway.restart_ib_gateway())

    assert result["restarted"] is False
    assert result["reason"] == "awaiting_backoff"


# --- Operator escape hatch must also release the lock ----------------------


def test_reset_restart_backoff_also_releases_2fa_lock():
    """`reset_restart_backoff` is the 'I just approved 2FA' button.
    It must clear the lock too, otherwise the next restart attempt
    would still be blocked by the in-flight lock."""
    ib_2fa_lock.acquire_2fa_push_lock("restart_ib_gateway", ttl_secs=600)
    assert ib_2fa_lock.check_2fa_push_lock() is not None

    ib_gateway.reset_restart_backoff()

    assert ib_2fa_lock.check_2fa_push_lock() is None


# --- TTL safety net --------------------------------------------------------


def test_expired_lock_does_not_block_new_restart(monkeypatch):
    """Defense in depth: if a holder crashes without releasing, the
    lock auto-expires and other restart paths get to proceed once the
    TTL passes."""
    # Hand-write an expired lock to disk.
    ib_2fa_lock.acquire_2fa_push_lock("crashed-holder", ttl_secs=1, now=time.time() - 10)

    _patch_docker_mode(
        monkeypatch,
        restart_result={"restarted": True, "port_listening": True, "gateway_mode": "docker"},
        probe_result=(False, []),
    )

    result = asyncio.run(ib_gateway.restart_ib_gateway())
    assert result["restarted"] is True
    # Fresh holder now owns the lock.
    held = ib_2fa_lock.check_2fa_push_lock()
    assert held is not None
    assert "restart_ib_gateway" in held.holder


def test_ensure_docker_refuses_start_when_push_lease_is_held(monkeypatch):
    ib_2fa_lock.acquire_2fa_push_lock("ib-watchdog", ttl_secs=600)
    monkeypatch.setattr(ib_gateway, "_port_listening", lambda: False)

    async def exited():
        return "exited", ""

    async def fail_compose(*args, **kwargs):
        raise AssertionError("ensure must not start while another push lease is active")

    monkeypatch.setattr(ib_gateway, "_docker_container_state", exited)
    monkeypatch.setattr(ib_gateway, "_docker_compose", fail_compose)

    result = asyncio.run(ib_gateway._ensure_docker_container())

    assert result["restarted"] is False
    assert result["deferred"] is True
    assert result["reason"] == "2fa_push_in_flight"


def test_ensure_docker_acquires_lease_before_start(monkeypatch):
    monkeypatch.setattr(ib_gateway, "_port_listening", lambda: False)

    async def exited():
        return "exited", ""

    async def compose(*args, **kwargs):
        return "", "", 0

    async def port_ready(*args, **kwargs):
        return True, 3

    monkeypatch.setattr(ib_gateway, "_docker_container_state", exited)
    monkeypatch.setattr(ib_gateway, "_docker_compose", compose)
    monkeypatch.setattr(ib_gateway, "_poll_port", port_ready)

    result = asyncio.run(ib_gateway._ensure_docker_container())

    assert result["port_listening"] is True
    held = ib_2fa_lock.check_2fa_push_lock()
    assert held is not None
    assert "ensure_ib_gateway" in held.holder


def test_restart_stopped_docker_reuses_preheld_restart_lease(monkeypatch):
    ib_2fa_lock.acquire_2fa_push_lock(ib_gateway.IB_GATEWAY_LOCK_HOLDER, ttl_secs=600)
    monkeypatch.setattr(ib_gateway, "_port_listening", lambda: False)

    async def exited():
        return "exited", ""

    compose_calls = []

    async def compose(*args, **kwargs):
        compose_calls.append(args)
        return "", "", 0

    async def port_ready(*args, **kwargs):
        return True, 3

    def fail_acquire(*args, **kwargs):
        raise AssertionError("stopped-container restart must reuse its preheld lease")

    monkeypatch.setattr(ib_gateway, "_docker_container_state", exited)
    monkeypatch.setattr(ib_gateway, "_docker_compose", compose)
    monkeypatch.setattr(ib_gateway, "_poll_port", port_ready)
    monkeypatch.setattr(ib_gateway, "_acquire_gateway_push_lease", fail_acquire)

    result = asyncio.run(ib_gateway._restart_docker())

    assert result["port_listening"] is True
    assert compose_calls == [("up", "-d")]
    held = ib_2fa_lock.check_2fa_push_lock()
    assert held is not None and held.holder == ib_gateway.IB_GATEWAY_LOCK_HOLDER


def test_cloud_restart_returns_before_push_lease_acquisition(monkeypatch):
    lock_path = Path(os.environ["IB_2FA_LOCK_PATH"])
    monkeypatch.setattr(ib_gateway, "is_cloud_mode", lambda: True)

    def fail_acquire(*args, **kwargs):
        raise AssertionError("cloud-mode restart must not acquire a local push lease")

    monkeypatch.setattr(ib_gateway.ib_2fa_lock, "acquire_2fa_push_lock", fail_acquire)

    result = asyncio.run(ib_gateway.restart_ib_gateway())

    assert result["gateway_mode"] == "cloud"
    assert result["restarted"] is False
    assert not lock_path.exists()


def test_ensure_healthy_docker_does_not_touch_lease(monkeypatch):
    monkeypatch.setattr(ib_gateway, "_port_listening", lambda: True)

    def fail_acquire(*args, **kwargs):
        raise AssertionError("read-only ensure must not acquire a push lease")

    monkeypatch.setattr(ib_gateway.ib_2fa_lock, "acquire_2fa_push_lock", fail_acquire)

    result = asyncio.run(ib_gateway._ensure_docker_container())

    assert result["status"] == "already_running"


def test_authenticated_observation_releases_ensure_lease(monkeypatch):
    holder = "scripts.api.ib_gateway.ensure_ib_gateway"
    ib_2fa_lock.acquire_2fa_push_lock(holder, ttl_secs=600)
    pool = type("Pool", (), {"status": lambda self: {}})()
    ib_gateway._auth_transition_state["previous_auth_state"] = "awaiting_2fa"

    asyncio.run(ib_gateway.handle_auth_state_transition("authenticated", pool))

    assert ib_2fa_lock.check_2fa_push_lock() is None
