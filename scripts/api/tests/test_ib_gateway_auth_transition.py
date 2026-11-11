"""Auto-recovery for the documented IB pool stuck-after-2FA failure mode.

When `auth_state` transitions `awaiting_2fa → authenticated` (via the
periodic `/health` probe), the FastAPI IB connection pool clients (sync/orders/data) can stay
`connected: False`. The manual recovery was `systemctl restart radon-api.service`.
These tests pin the autonomous recovery contract:

  1. On `awaiting_2fa → authenticated` transition with any pool slot
     disconnected, the pool's `reconnect_all()` is invoked exactly once.
  2. On the same transition with a fully connected pool, reconnect is NOT
     triggered (no work to do).
  3. A steady-state `authenticated → authenticated` (no transition) does NOT
     paper over disconnected pool slots by triggering reconnect — the
     transition is the signal, not the pool state alone.
  4. The reconnect attempt is bounded by a timeout so a wedge cannot block
     the calling probe loop.

See feedback_ib_pool_stuck_after_2fa.md and feedback_ib_gateway_2fa_verification.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.api import ib_gateway


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state(tmp_path, monkeypatch):
    """Each test starts with clean auth-transition state.

    Also redirects the cross-process 2FA push lock to a tmp file so we
    never touch the production /var/lib/radon path during tests.
    """
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))
    ib_gateway._auth_transition_state["previous_auth_state"] = None
    ib_gateway._auth_transition_state["last_reconnect_at"] = 0.0
    yield
    ib_gateway._auth_transition_state["previous_auth_state"] = None
    ib_gateway._auth_transition_state["last_reconnect_at"] = 0.0


def _make_mock_pool(connected_roles: dict[str, bool], accounts: list[str] | None = None):
    """Build a MagicMock that quacks like IBPool for the transition handler.

    `connected_roles`: maps role name → is_connected bool.
    `accounts`: optional managed_accounts list (used in status()).
    """
    accounts = accounts if accounts is not None else ["U1234567"]
    pool = MagicMock()

    def status() -> dict:
        return {
            role: {
                "connected": connected_roles.get(role, False),
                "client_id": idx + 3,
                "managed_accounts": accounts if connected_roles.get(role, False) else [],
            }
            for idx, role in enumerate(("sync", "orders", "data"))
        }

    pool.status.side_effect = status
    pool.reconnect_all = AsyncMock(return_value={r: True for r in connected_roles})
    return pool


# ---------------------------------------------------------------------------
# Test (a): transition + dead pool → reconnect_all called once
# ---------------------------------------------------------------------------


def test_auth_transition_awaiting_2fa_to_authenticated_with_dead_pool_triggers_reconnect():
    """awaiting_2fa → authenticated with disconnected pool slot → reconnect_all called once."""
    ib_gateway._auth_transition_state["previous_auth_state"] = "awaiting_2fa"
    pool = _make_mock_pool({"sync": False, "orders": False, "data": False})

    triggered = asyncio.run(
        ib_gateway.handle_auth_state_transition(
            new_auth_state="authenticated",
            pool=pool,
        )
    )

    assert triggered is True, "expected reconnect to be triggered on transition with dead pool"
    pool.reconnect_all.assert_awaited_once()
    # Transition tracker now sees authenticated as the prior state for next probe
    assert ib_gateway._auth_transition_state["previous_auth_state"] == "authenticated"


# ---------------------------------------------------------------------------
# Test (b): transition + healthy pool → reconnect_all NOT called
# ---------------------------------------------------------------------------


def test_auth_transition_with_healthy_pool_does_not_trigger_reconnect():
    """awaiting_2fa → authenticated but all pool slots already connected → no reconnect."""
    ib_gateway._auth_transition_state["previous_auth_state"] = "awaiting_2fa"
    pool = _make_mock_pool({"sync": True, "orders": True, "data": True})

    triggered = asyncio.run(
        ib_gateway.handle_auth_state_transition(
            new_auth_state="authenticated",
            pool=pool,
        )
    )

    assert triggered is False
    pool.reconnect_all.assert_not_awaited()
    assert ib_gateway._auth_transition_state["previous_auth_state"] == "authenticated"


# ---------------------------------------------------------------------------
# Test (c): no transition + dead pool → reconnect_all NOT called
# ---------------------------------------------------------------------------


def test_no_transition_steady_authenticated_does_not_paper_over_disconnects():
    """authenticated → authenticated (steady-state) must NOT trigger reconnect
    even when pool slots are disconnected. Real disconnects mid-session are a
    separate concern handled by the per-role auto-reconnect in _PoolContext.
    """
    ib_gateway._auth_transition_state["previous_auth_state"] = "authenticated"
    pool = _make_mock_pool({"sync": False, "orders": True, "data": True})

    triggered = asyncio.run(
        ib_gateway.handle_auth_state_transition(
            new_auth_state="authenticated",
            pool=pool,
        )
    )

    assert triggered is False
    pool.reconnect_all.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test (d): reconnect bounded by timeout
# ---------------------------------------------------------------------------


def test_reconnect_bounded_by_timeout_logs_and_returns(caplog):
    """A wedge in reconnect_all() must not block the probe loop — the call is
    wrapped in asyncio.wait_for with a 30s ceiling. We simulate a hanging
    reconnect by patching the timeout to a tiny value and using a slow mock.
    """
    ib_gateway._auth_transition_state["previous_auth_state"] = "awaiting_2fa"
    pool = MagicMock()
    pool.status.side_effect = lambda: {
        "sync": {"connected": False, "client_id": 3, "managed_accounts": []},
        "orders": {"connected": False, "client_id": 4, "managed_accounts": []},
        "data": {"connected": False, "client_id": 5, "managed_accounts": []},
    }

    async def slow_reconnect():
        await asyncio.sleep(10)
        return {"sync": True, "orders": True, "data": True}

    pool.reconnect_all = slow_reconnect

    caplog.set_level(logging.WARNING, logger="radon.ib_gateway")

    triggered = asyncio.run(
        ib_gateway.handle_auth_state_transition(
            new_auth_state="authenticated",
            pool=pool,
            reconnect_timeout=0.05,
        )
    )

    # Reconnect was attempted but timed out — return value still True (we tried)
    # and the timeout is surfaced via WARNING-level log.
    assert triggered is True
    timeout_logs = [r for r in caplog.records if "timed out" in r.getMessage().lower()]
    assert timeout_logs, "expected a timeout warning to be logged"


# ---------------------------------------------------------------------------
# Bonus: ensure transition tracker also tolerates None previous state
# (cold start) — first probe should NOT trigger reconnect even if auth is
# already authenticated and pool is dead. We only act on actual transitions.
# ---------------------------------------------------------------------------


def test_cold_start_first_probe_does_not_trigger_reconnect():
    """On the very first probe (previous_auth_state=None), we observe and
    record but do not act — a real awaiting_2fa→authenticated edge is
    required."""
    assert ib_gateway._auth_transition_state["previous_auth_state"] is None
    pool = _make_mock_pool({"sync": False, "orders": False, "data": False})

    triggered = asyncio.run(
        ib_gateway.handle_auth_state_transition(
            new_auth_state="authenticated",
            pool=pool,
        )
    )

    assert triggered is False
    pool.reconnect_all.assert_not_awaited()
    assert ib_gateway._auth_transition_state["previous_auth_state"] == "authenticated"


# ---------------------------------------------------------------------------
# Idempotency: calling twice in quick succession is safe (re-entry guard).
# ---------------------------------------------------------------------------


def test_idempotent_reconnect_within_short_window():
    """Two transitions back-to-back must not double-fire reconnect. After the
    first triggers, the second call (with prior already moved to authenticated)
    is treated as no-transition.
    """
    ib_gateway._auth_transition_state["previous_auth_state"] = "awaiting_2fa"
    pool = _make_mock_pool({"sync": False, "orders": False, "data": False})

    first = asyncio.run(
        ib_gateway.handle_auth_state_transition(
            new_auth_state="authenticated",
            pool=pool,
        )
    )
    second = asyncio.run(
        ib_gateway.handle_auth_state_transition(
            new_auth_state="authenticated",
            pool=pool,
        )
    )

    assert first is True
    assert second is False
    pool.reconnect_all.assert_awaited_once()


# ---------------------------------------------------------------------------
# Integration: check_ib_gateway invokes the transition handler
# ---------------------------------------------------------------------------


def test_check_ib_gateway_drives_transition_handler(monkeypatch):
    """When `/health` is hit and the derived auth_state shifts from awaiting_2fa
    to authenticated, the pool reconnect must be triggered as a side-effect of
    the probe — no separate scheduler required.
    """
    ib_gateway._auth_transition_state["previous_auth_state"] = "awaiting_2fa"
    pool = _make_mock_pool({"sync": False, "orders": False, "data": False})

    # Pool status: connected=True with managed_accounts populated → authenticated.
    pool_status = {
        "sync": {"connected": True, "client_id": 3, "managed_accounts": ["U1234567"]},
        "orders": {"connected": True, "client_id": 4, "managed_accounts": ["U1234567"]},
        "data": {"connected": True, "client_id": 5, "managed_accounts": ["U1234567"]},
    }

    # But the *transition handler* sees the live pool object (pool.status())
    # which we control via the mock and which reports disconnected slots →
    # reconnect should fire.

    async def fake_check_cloud():
        return {
            "port_listening": True,
            "upstream_dead": False,
            "service_state": "reachable",
            "host": "127.0.0.1",
            "port": 4001,
            "gateway_mode": "cloud",
        }

    monkeypatch.setattr(ib_gateway, "_check_cloud", fake_check_cloud)

    result = asyncio.run(ib_gateway.check_ib_gateway(pool_status=pool_status, pool=pool))

    assert result["auth_state"] == "authenticated"
    pool.reconnect_all.assert_awaited_once()


