"""FastAPI must never stack an IBKR Mobile 2FA push.

The bug this guards against (incident 2026-05-19): the user ran
``radon restart``, IB Gateway came up at ``awaiting_2fa``, the user
approved the push on their phone, IBKR reported "unsuccessful". Root
cause: a second restart fired ~3 minutes later from ``ib_watchdog.py``
hitting its api-hang threshold, sending a SECOND 2FA push while the
first was still pending. IBKR's backend cannot reconcile stacked
pushes — every approval gets rejected.

The Gateway's owner (the broker helper) holds the shared push lock for
every cycle. This process only probes it, so these tests pin down that:

  • ``restart_ib_gateway`` and ``ensure_ib_gateway`` never take or write
    the cross-process push lease.
  • ``reset_restart_backoff`` (operator escape hatch, "I just approved
    2FA") releases any in-flight lease.
  • An authenticated observation clears a startup lease an older process
    may have left behind.
"""

from __future__ import annotations

import asyncio
import os
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


def _fail_acquire(*args, **kwargs):
    raise AssertionError("FastAPI must not acquire a local push lease")


# --- Operator escape hatch must release the lock ---------------------------


def test_reset_restart_backoff_also_releases_2fa_lock():
    """`reset_restart_backoff` is the 'I just approved 2FA' button.
    It must clear the lock, otherwise the next restart attempt from the
    Gateway's owner would still be blocked by the in-flight lock."""
    ib_2fa_lock.acquire_2fa_push_lock("restart_ib_gateway", ttl_secs=600)
    assert ib_2fa_lock.check_2fa_push_lock() is not None

    ib_gateway.reset_restart_backoff()

    assert ib_2fa_lock.check_2fa_push_lock() is None


# --- This process never takes the lease -------------------------------------


def test_cloud_restart_returns_before_push_lease_acquisition(monkeypatch):
    lock_path = Path(os.environ["IB_2FA_LOCK_PATH"])
    monkeypatch.setattr(ib_gateway.ib_2fa_lock, "acquire_2fa_push_lock", _fail_acquire)

    result = asyncio.run(ib_gateway.restart_ib_gateway())

    assert result["gateway_mode"] == "cloud"
    assert result["restarted"] is False
    assert not lock_path.exists()


@pytest.mark.parametrize("port_up", [True, False])
def test_ensure_never_touches_lease(monkeypatch, port_up):
    lock_path = Path(os.environ["IB_2FA_LOCK_PATH"])
    monkeypatch.setattr(ib_gateway, "_port_listening", lambda *a, **k: port_up)
    monkeypatch.setattr(ib_gateway.ib_2fa_lock, "acquire_2fa_push_lock", _fail_acquire)

    result = asyncio.run(ib_gateway.ensure_ib_gateway())

    assert result["gateway_mode"] == "cloud"
    assert result["port_listening"] is port_up
    assert not lock_path.exists()


def test_authenticated_observation_releases_ensure_lease(monkeypatch):
    holder = "scripts.api.ib_gateway.ensure_ib_gateway"
    ib_2fa_lock.acquire_2fa_push_lock(holder, ttl_secs=600)
    pool = type("Pool", (), {"status": lambda self: {}})()
    ib_gateway._auth_transition_state["previous_auth_state"] = "awaiting_2fa"

    asyncio.run(ib_gateway.handle_auth_state_transition("authenticated", pool))

    assert ib_2fa_lock.check_2fa_push_lock() is None
